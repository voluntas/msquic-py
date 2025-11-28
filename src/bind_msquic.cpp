// nanobind
#include <nanobind/nanobind.h>
#include <nanobind/stl/function.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/shared_ptr.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/vector.h>

// msquic
#include <msquic.h>

// quic_var_int.h を使用するために必要なマクロを定義
#ifndef QUIC_INLINE
#define QUIC_INLINE static inline
#endif
#ifndef CXPLAT_DBG_ASSERT
#define CXPLAT_DBG_ASSERT(exp)
#endif
#ifndef CXPLAT_ANALYSIS_ASSERT
#define CXPLAT_ANALYSIS_ASSERT(exp)
#endif
#ifndef CxPlatByteSwapUint16
#define CxPlatByteSwapUint16(value) __builtin_bswap16((unsigned short)(value))
#endif
#ifndef CxPlatByteSwapUint32
#define CxPlatByteSwapUint32(value) __builtin_bswap32((value))
#endif
#ifndef CxPlatByteSwapUint64
#define CxPlatByteSwapUint64(value) __builtin_bswap64((value))
#endif

#include <quic_var_int.h>

#include <atomic>
#include <cstring>
#include <functional>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

namespace nb = nanobind;
using namespace nb::literals;

namespace msquic_py {

// グローバル API テーブル
const QUIC_API_TABLE* g_MsQuic = nullptr;
std::mutex g_MsQuicMutex;

// API を開く
void open_api() {
  std::lock_guard<std::mutex> lock(g_MsQuicMutex);
  if (g_MsQuic == nullptr) {
    QUIC_STATUS status = MsQuicOpenVersion(QUIC_API_VERSION_2, (const void**)&g_MsQuic);
    if (QUIC_FAILED(status)) {
      throw std::runtime_error("Failed to open MsQuic API");
    }
  }
}

// API を閉じる
void close_api() {
  std::lock_guard<std::mutex> lock(g_MsQuicMutex);
  if (g_MsQuic != nullptr) {
    MsQuicClose(g_MsQuic);
    g_MsQuic = nullptr;
  }
}

// 前方宣言
class Stream;
class Connection;

// ========== Registration ==========
class Registration {
 public:
  Registration(const std::string& app_name, QUIC_EXECUTION_PROFILE profile) {
    open_api();
    QUIC_REGISTRATION_CONFIG config = {0};
    config.AppName = app_name.c_str();
    config.ExecutionProfile = profile;
    QUIC_STATUS status = g_MsQuic->RegistrationOpen(&config, &handle_);
    if (QUIC_FAILED(status)) {
      throw std::runtime_error("Failed to open registration");
    }
  }

  ~Registration() {
    close();
  }

  HQUIC handle() const { return handle_; }

  void shutdown(QUIC_CONNECTION_SHUTDOWN_FLAGS flags, uint64_t error_code) {
    if (handle_ != nullptr && g_MsQuic != nullptr) {
      g_MsQuic->RegistrationShutdown(handle_, flags, error_code);
    }
  }

  void close() {
    if (handle_ != nullptr && g_MsQuic != nullptr) {
      // GIL を解放して MsQuic API を呼び出す
      nb::gil_scoped_release release;
      g_MsQuic->RegistrationClose(handle_);
      handle_ = nullptr;
    }
  }

 private:
  HQUIC handle_ = nullptr;
};

// ========== Configuration ==========
class Configuration {
 public:
  Configuration(Registration& registration,
                const std::vector<std::string>& alpn_list,
                uint64_t idle_timeout_ms = 0,
                uint16_t peer_bidi_stream_count = 0,
                uint16_t peer_unidi_stream_count = 0) {
    // ALPN バッファを構築
    std::vector<QUIC_BUFFER> alpn_buffers;
    for (const auto& alpn : alpn_list) {
      QUIC_BUFFER buf;
      buf.Length = static_cast<uint32_t>(alpn.size());
      buf.Buffer = (uint8_t*)alpn.data();
      alpn_buffers.push_back(buf);
    }

    // Settings を構築
    QUIC_SETTINGS settings = {0};
    if (idle_timeout_ms > 0) {
      settings.IdleTimeoutMs = idle_timeout_ms;
      settings.IsSet.IdleTimeoutMs = TRUE;
    }
    if (peer_bidi_stream_count > 0) {
      settings.PeerBidiStreamCount = peer_bidi_stream_count;
      settings.IsSet.PeerBidiStreamCount = TRUE;
    }
    if (peer_unidi_stream_count > 0) {
      settings.PeerUnidiStreamCount = peer_unidi_stream_count;
      settings.IsSet.PeerUnidiStreamCount = TRUE;
    }

    QUIC_STATUS status = g_MsQuic->ConfigurationOpen(
        registration.handle(),
        alpn_buffers.data(),
        static_cast<uint32_t>(alpn_buffers.size()),
        &settings,
        sizeof(settings),
        nullptr,
        &handle_);
    if (QUIC_FAILED(status)) {
      throw std::runtime_error("Failed to open configuration");
    }
  }

  ~Configuration() {
    close();
  }

  HQUIC handle() const { return handle_; }

  void load_credential_file(const std::string& cert_file,
                            const std::string& key_file,
                            bool is_client = false) {
    QUIC_CREDENTIAL_CONFIG cred_config = {};
    cred_config.Type = QUIC_CREDENTIAL_TYPE_CERTIFICATE_FILE;

    QUIC_CERTIFICATE_FILE cert_file_config = {};
    cert_file_config.CertificateFile = cert_file.c_str();
    cert_file_config.PrivateKeyFile = key_file.c_str();
    cred_config.CertificateFile = &cert_file_config;

    if (is_client) {
      cred_config.Flags = QUIC_CREDENTIAL_FLAG_CLIENT;
    } else {
      cred_config.Flags = QUIC_CREDENTIAL_FLAG_NONE;
    }

    QUIC_STATUS status = g_MsQuic->ConfigurationLoadCredential(handle_, &cred_config);
    if (QUIC_FAILED(status)) {
      throw std::runtime_error("Failed to load credential");
    }
  }

  void load_credential_none(bool no_certificate_validation = false) {
    QUIC_CREDENTIAL_CONFIG cred_config = {};
    cred_config.Type = QUIC_CREDENTIAL_TYPE_NONE;
    cred_config.Flags = QUIC_CREDENTIAL_FLAG_CLIENT;
    if (no_certificate_validation) {
      cred_config.Flags |= QUIC_CREDENTIAL_FLAG_NO_CERTIFICATE_VALIDATION;
    }

    QUIC_STATUS status = g_MsQuic->ConfigurationLoadCredential(handle_, &cred_config);
    if (QUIC_FAILED(status)) {
      throw std::runtime_error("Failed to load credential");
    }
  }

  void close() {
    if (handle_ != nullptr && g_MsQuic != nullptr) {
      // GIL を解放して MsQuic API を呼び出す
      nb::gil_scoped_release release;
      g_MsQuic->ConfigurationClose(handle_);
      handle_ = nullptr;
    }
  }

 private:
  HQUIC handle_ = nullptr;
};

// ========== Stream ==========
// Stream コールバック用のコンテキスト
struct StreamContext {
  std::mutex mutex;
  std::atomic<bool> is_closing{false};
  HQUIC handle = nullptr;
  std::function<void(const std::vector<uint8_t>&, bool)> on_receive;
  std::function<void()> on_send_complete;
  std::function<void(uint64_t)> on_peer_send_aborted;
  std::function<void(uint64_t)> on_peer_receive_aborted;
  std::function<void(bool)> on_shutdown_complete;
};

// Stream コールバック（前方宣言）
QUIC_STATUS QUIC_API StreamCallback(HQUIC stream, void* context, QUIC_STREAM_EVENT* event);

class Stream {
 public:
  Stream(HQUIC handle) : handle_(handle) {
    context_ = std::make_unique<StreamContext>();
    context_->handle = handle;
  }

  ~Stream() {
    // SHUTDOWN_COMPLETE で Close されていない場合のフォールバック
    // ただし、通常は SHUTDOWN_COMPLETE で Close される
  }

  HQUIC handle() const { return handle_; }
  StreamContext* context() const { return context_.get(); }

  void start(QUIC_STREAM_START_FLAGS flags = QUIC_STREAM_START_FLAG_NONE) {
    QUIC_STATUS status = g_MsQuic->StreamStart(handle_, flags);
    if (QUIC_FAILED(status)) {
      throw std::runtime_error("Failed to start stream");
    }
  }

  void send(const nb::bytes& data, QUIC_SEND_FLAGS flags = QUIC_SEND_FLAG_NONE) {
    // データをコピーして保持
    auto* buf_data = new uint8_t[data.size()];
    std::memcpy(buf_data, data.c_str(), data.size());

    auto* buffer = new QUIC_BUFFER;
    buffer->Length = static_cast<uint32_t>(data.size());
    buffer->Buffer = buf_data;

    QUIC_STATUS status;
    {
      // GIL を解放して MsQuic API を呼び出す
      nb::gil_scoped_release release;
      status = g_MsQuic->StreamSend(handle_, buffer, 1, flags, buffer);
    }
    if (QUIC_FAILED(status)) {
      delete[] buf_data;
      delete buffer;
      throw std::runtime_error("Failed to send data");
    }
  }

  void shutdown(QUIC_STREAM_SHUTDOWN_FLAGS flags, uint64_t error_code = 0) {
    QUIC_STATUS status = g_MsQuic->StreamShutdown(handle_, flags, error_code);
    if (QUIC_FAILED(status)) {
      throw std::runtime_error("Failed to shutdown stream");
    }
  }

  void set_on_receive(std::function<void(const std::vector<uint8_t>&, bool)> callback) {
    std::lock_guard<std::mutex> lock(context_->mutex);
    context_->on_receive = std::move(callback);
  }

  void set_on_send_complete(std::function<void()> callback) {
    std::lock_guard<std::mutex> lock(context_->mutex);
    context_->on_send_complete = std::move(callback);
  }

  void set_on_shutdown_complete(std::function<void(bool)> callback) {
    std::lock_guard<std::mutex> lock(context_->mutex);
    context_->on_shutdown_complete = std::move(callback);
  }

 private:
  HQUIC handle_ = nullptr;
  std::unique_ptr<StreamContext> context_;
};

// Stream コールバック
QUIC_STATUS QUIC_API StreamCallback(HQUIC stream, void* context, QUIC_STREAM_EVENT* event) {
  auto* ctx = static_cast<StreamContext*>(context);
  if (!ctx || ctx->is_closing.load()) {
    return QUIC_STATUS_SUCCESS;
  }

  switch (event->Type) {
    case QUIC_STREAM_EVENT_RECEIVE: {
      std::function<void(const std::vector<uint8_t>&, bool)> callback;
      {
        std::lock_guard<std::mutex> lock(ctx->mutex);
        callback = ctx->on_receive;
      }
      if (callback) {
        std::vector<uint8_t> data;
        for (uint32_t i = 0; i < event->RECEIVE.BufferCount; i++) {
          const auto& buf = event->RECEIVE.Buffers[i];
          data.insert(data.end(), buf.Buffer, buf.Buffer + buf.Length);
        }
        bool fin = (event->RECEIVE.Flags & QUIC_RECEIVE_FLAG_FIN) != 0;
        nb::gil_scoped_acquire acquire;
        callback(data, fin);
      }
      break;
    }
    case QUIC_STREAM_EVENT_SEND_COMPLETE: {
      // 送信バッファを解放
      auto* buffer = static_cast<QUIC_BUFFER*>(event->SEND_COMPLETE.ClientContext);
      if (buffer) {
        delete[] buffer->Buffer;
        delete buffer;
      }
      std::function<void()> callback;
      {
        std::lock_guard<std::mutex> lock(ctx->mutex);
        callback = ctx->on_send_complete;
      }
      if (callback) {
        nb::gil_scoped_acquire acquire;
        callback();
      }
      break;
    }
    case QUIC_STREAM_EVENT_PEER_SEND_ABORTED: {
      std::function<void(uint64_t)> callback;
      {
        std::lock_guard<std::mutex> lock(ctx->mutex);
        callback = ctx->on_peer_send_aborted;
      }
      if (callback) {
        nb::gil_scoped_acquire acquire;
        callback(event->PEER_SEND_ABORTED.ErrorCode);
      }
      break;
    }
    case QUIC_STREAM_EVENT_PEER_RECEIVE_ABORTED: {
      std::function<void(uint64_t)> callback;
      {
        std::lock_guard<std::mutex> lock(ctx->mutex);
        callback = ctx->on_peer_receive_aborted;
      }
      if (callback) {
        nb::gil_scoped_acquire acquire;
        callback(event->PEER_RECEIVE_ABORTED.ErrorCode);
      }
      break;
    }
    case QUIC_STREAM_EVENT_SHUTDOWN_COMPLETE: {
      // コールバックを呼び出す
      std::function<void(bool)> callback;
      {
        std::lock_guard<std::mutex> lock(ctx->mutex);
        callback = ctx->on_shutdown_complete;
      }
      if (callback) {
        nb::gil_scoped_acquire acquire;
        callback(event->SHUTDOWN_COMPLETE.ConnectionShutdown);
      }
      // 循環参照を解消するためにコールバックをクリアする
      {
        nb::gil_scoped_acquire acquire;
        std::lock_guard<std::mutex> lock(ctx->mutex);
        ctx->on_receive = nullptr;
        ctx->on_send_complete = nullptr;
        ctx->on_peer_send_aborted = nullptr;
        ctx->on_peer_receive_aborted = nullptr;
        ctx->on_shutdown_complete = nullptr;
      }
      // MsQuic のパターン: SHUTDOWN_COMPLETE で StreamClose を呼び出す
      // AppCloseInProgress が true の場合、アプリが既に Close を呼んでいるのでスキップ
      if (!event->SHUTDOWN_COMPLETE.AppCloseInProgress) {
        ctx->is_closing.store(true);
        g_MsQuic->StreamClose(stream);
      }
      break;
    }
    default:
      break;
  }
  return QUIC_STATUS_SUCCESS;
}

// ========== Connection ==========
struct ConnectionContext {
  std::mutex mutex;
  std::atomic<bool> is_closing{false};
  HQUIC handle = nullptr;
  std::function<void()> on_connected;
  std::function<void(bool)> on_shutdown_complete;
  std::function<void(std::shared_ptr<Stream>)> on_peer_stream_started;
  std::vector<std::shared_ptr<Stream>> streams;
};

// Connection コールバック（前方宣言）
QUIC_STATUS QUIC_API ConnectionCallback(HQUIC connection, void* context, QUIC_CONNECTION_EVENT* event);

class Connection {
 public:
  Connection(Registration& registration) : registration_(&registration) {
    context_ = std::make_unique<ConnectionContext>();
    QUIC_STATUS status = g_MsQuic->ConnectionOpen(
        registration.handle(),
        ConnectionCallback,
        context_.get(),
        &handle_);
    if (QUIC_FAILED(status)) {
      throw std::runtime_error("Failed to open connection");
    }
    context_->handle = handle_;
  }

  // サーバー側から受け入れた接続用
  Connection(HQUIC handle) : handle_(handle), registration_(nullptr) {
    context_ = std::make_unique<ConnectionContext>();
    context_->handle = handle;
    g_MsQuic->SetCallbackHandler(handle_, (void*)ConnectionCallback, context_.get());
  }

  ~Connection() {
    // SHUTDOWN_COMPLETE で Close されていない場合のフォールバック
    // ただし、通常は SHUTDOWN_COMPLETE で Close される
  }

  HQUIC handle() const { return handle_; }
  ConnectionContext* context() const { return context_.get(); }

  void start(Configuration& config, const std::string& server_name, uint16_t port) {
    QUIC_STATUS status = g_MsQuic->ConnectionStart(
        handle_,
        config.handle(),
        QUIC_ADDRESS_FAMILY_UNSPEC,
        server_name.c_str(),
        port);
    if (QUIC_FAILED(status)) {
      throw std::runtime_error("Failed to start connection");
    }
  }

  void set_configuration(Configuration& config) {
    QUIC_STATUS status = g_MsQuic->ConnectionSetConfiguration(handle_, config.handle());
    if (QUIC_FAILED(status)) {
      throw std::runtime_error("Failed to set configuration");
    }
  }

  void shutdown(QUIC_CONNECTION_SHUTDOWN_FLAGS flags, uint64_t error_code) {
    g_MsQuic->ConnectionShutdown(handle_, flags, error_code);
  }

  std::shared_ptr<Stream> open_stream(QUIC_STREAM_OPEN_FLAGS flags = QUIC_STREAM_OPEN_FLAG_NONE) {
    HQUIC stream_handle = nullptr;
    QUIC_STATUS status = g_MsQuic->StreamOpen(
        handle_,
        flags,
        StreamCallback,
        nullptr,
        &stream_handle);
    if (QUIC_FAILED(status)) {
      throw std::runtime_error("Failed to open stream");
    }
    auto stream = std::make_shared<Stream>(stream_handle);
    // コールバックのコンテキストを設定
    g_MsQuic->SetCallbackHandler(stream_handle, (void*)StreamCallback, stream->context());
    {
      std::lock_guard<std::mutex> lock(context_->mutex);
      context_->streams.push_back(stream);
    }
    return stream;
  }

  void set_on_connected(std::function<void()> callback) {
    std::lock_guard<std::mutex> lock(context_->mutex);
    context_->on_connected = std::move(callback);
  }

  void set_on_shutdown_complete(std::function<void(bool)> callback) {
    std::lock_guard<std::mutex> lock(context_->mutex);
    context_->on_shutdown_complete = std::move(callback);
  }

  void set_on_peer_stream_started(std::function<void(std::shared_ptr<Stream>)> callback) {
    std::lock_guard<std::mutex> lock(context_->mutex);
    context_->on_peer_stream_started = std::move(callback);
  }

 private:
  HQUIC handle_ = nullptr;
  Registration* registration_;
  std::unique_ptr<ConnectionContext> context_;
};

QUIC_STATUS QUIC_API ConnectionCallback(HQUIC connection, void* context, QUIC_CONNECTION_EVENT* event) {
  auto* ctx = static_cast<ConnectionContext*>(context);
  if (!ctx || ctx->is_closing.load()) {
    return QUIC_STATUS_SUCCESS;
  }

  switch (event->Type) {
    case QUIC_CONNECTION_EVENT_CONNECTED: {
      std::function<void()> callback;
      {
        std::lock_guard<std::mutex> lock(ctx->mutex);
        callback = ctx->on_connected;
      }
      if (callback) {
        nb::gil_scoped_acquire acquire;
        callback();
      }
      break;
    }
    case QUIC_CONNECTION_EVENT_SHUTDOWN_COMPLETE: {
      // コールバックを呼び出す
      std::function<void(bool)> callback;
      {
        std::lock_guard<std::mutex> lock(ctx->mutex);
        callback = ctx->on_shutdown_complete;
      }
      if (callback) {
        nb::gil_scoped_acquire acquire;
        callback(event->SHUTDOWN_COMPLETE.AppCloseInProgress);
      }
      // 循環参照を解消するためにコールバックと streams をクリアする
      {
        nb::gil_scoped_acquire acquire;
        std::lock_guard<std::mutex> lock(ctx->mutex);
        ctx->on_connected = nullptr;
        ctx->on_shutdown_complete = nullptr;
        ctx->on_peer_stream_started = nullptr;
        ctx->streams.clear();
      }
      // MsQuic のパターン: SHUTDOWN_COMPLETE で ConnectionClose を呼び出す
      // AppCloseInProgress が true の場合、アプリが既に Close を呼んでいるのでスキップ
      if (!event->SHUTDOWN_COMPLETE.AppCloseInProgress) {
        ctx->is_closing.store(true);
        g_MsQuic->ConnectionClose(connection);
      }
      break;
    }
    case QUIC_CONNECTION_EVENT_PEER_STREAM_STARTED: {
      // Stream オブジェクトを作成
      auto stream = std::make_shared<Stream>(event->PEER_STREAM_STARTED.Stream);
      {
        std::lock_guard<std::mutex> lock(ctx->mutex);
        ctx->streams.push_back(stream);
      }

      // 先に Python コールバックを呼んで on_receive を設定させる
      // SetCallbackHandler の前に呼ばないと、RECEIVE イベントが来た時に
      // on_receive が未設定でデータが失われる
      std::function<void(std::shared_ptr<Stream>)> callback;
      {
        std::lock_guard<std::mutex> lock(ctx->mutex);
        callback = ctx->on_peer_stream_started;
      }
      if (callback) {
        nb::gil_scoped_acquire acquire;
        callback(stream);
      }

      // Python が on_receive を設定した後にコールバックを有効化
      g_MsQuic->SetCallbackHandler(
          event->PEER_STREAM_STARTED.Stream,
          (void*)StreamCallback,
          stream->context());
      break;
    }
    default:
      break;
  }
  return QUIC_STATUS_SUCCESS;
}

// ========== Listener ==========
struct ListenerContext {
  std::mutex mutex;
  std::atomic<bool> is_closing{false};
  HQUIC handle = nullptr;
  std::function<void(std::shared_ptr<Connection>)> on_new_connection;
  Configuration* config = nullptr;
  std::vector<std::shared_ptr<Connection>> connections;
};

// Listener コールバック（前方宣言）
QUIC_STATUS QUIC_API ListenerCallback(HQUIC listener, void* context, QUIC_LISTENER_EVENT* event);

class Listener {
 public:
  Listener(Registration& registration) {
    context_ = std::make_unique<ListenerContext>();
    QUIC_STATUS status = g_MsQuic->ListenerOpen(
        registration.handle(),
        ListenerCallback,
        context_.get(),
        &handle_);
    if (QUIC_FAILED(status)) {
      throw std::runtime_error("Failed to open listener");
    }
    context_->handle = handle_;
  }

  ~Listener() {
    close();
  }

  void start(Configuration& config, const std::vector<std::string>& alpn_list, uint16_t port) {
    context_->config = &config;

    // ALPN バッファを構築
    alpn_buffers_.clear();
    for (const auto& alpn : alpn_list) {
      QUIC_BUFFER buf;
      buf.Length = static_cast<uint32_t>(alpn.size());
      buf.Buffer = (uint8_t*)alpn.data();
      alpn_buffers_.push_back(buf);
    }

    QUIC_ADDR addr = {0};
    QuicAddrSetFamily(&addr, QUIC_ADDRESS_FAMILY_UNSPEC);
    QuicAddrSetPort(&addr, port);

    QUIC_STATUS status = g_MsQuic->ListenerStart(
        handle_,
        alpn_buffers_.data(),
        static_cast<uint32_t>(alpn_buffers_.size()),
        &addr);
    if (QUIC_FAILED(status)) {
      throw std::runtime_error("Failed to start listener");
    }
  }

  void stop() {
    if (handle_ != nullptr && g_MsQuic != nullptr) {
      context_->is_closing.store(true);
      // GIL を解放して MsQuic API を呼び出す
      nb::gil_scoped_release release;
      g_MsQuic->ListenerStop(handle_);
    }
  }

  void close() {
    if (handle_ != nullptr && g_MsQuic != nullptr) {
      context_->is_closing.store(true);
      // GIL を解放して MsQuic API を呼び出す
      nb::gil_scoped_release release;
      g_MsQuic->ListenerClose(handle_);
      handle_ = nullptr;
    }
  }

  void set_on_new_connection(std::function<void(std::shared_ptr<Connection>)> callback) {
    std::lock_guard<std::mutex> lock(context_->mutex);
    context_->on_new_connection = std::move(callback);
  }

 private:
  HQUIC handle_ = nullptr;
  std::unique_ptr<ListenerContext> context_;
  std::vector<QUIC_BUFFER> alpn_buffers_;
};

QUIC_STATUS QUIC_API ListenerCallback(HQUIC listener, void* context, QUIC_LISTENER_EVENT* event) {
  auto* ctx = static_cast<ListenerContext*>(context);
  if (!ctx || ctx->is_closing.load()) {
    return QUIC_STATUS_SUCCESS;
  }

  switch (event->Type) {
    case QUIC_LISTENER_EVENT_NEW_CONNECTION: {
      // Connection オブジェクトを作成
      auto connection = std::make_shared<Connection>(event->NEW_CONNECTION.Connection);
      {
        std::lock_guard<std::mutex> lock(ctx->mutex);
        ctx->connections.push_back(connection);
      }

      // Configuration を設定
      if (ctx->config) {
        QUIC_STATUS status = g_MsQuic->ConnectionSetConfiguration(
            event->NEW_CONNECTION.Connection,
            ctx->config->handle());
        if (QUIC_FAILED(status)) {
          return status;
        }
      }

      std::function<void(std::shared_ptr<Connection>)> callback;
      {
        std::lock_guard<std::mutex> lock(ctx->mutex);
        callback = ctx->on_new_connection;
      }
      if (callback) {
        nb::gil_scoped_acquire acquire;
        callback(connection);
      }
      break;
    }
    case QUIC_LISTENER_EVENT_STOP_COMPLETE: {
      // 循環参照を解消するためにコールバックと connections をクリアする
      {
        nb::gil_scoped_acquire acquire;
        std::lock_guard<std::mutex> lock(ctx->mutex);
        ctx->on_new_connection = nullptr;
        ctx->connections.clear();
      }
      break;
    }
    default:
      break;
  }
  return QUIC_STATUS_SUCCESS;
}

// ========== Enums バインディング ==========
void bind_enums(nb::module_& m) {
  nb::enum_<QUIC_EXECUTION_PROFILE>(m, "ExecutionProfile")
      .value("LOW_LATENCY", QUIC_EXECUTION_PROFILE_LOW_LATENCY)
      .value("MAX_THROUGHPUT", QUIC_EXECUTION_PROFILE_TYPE_MAX_THROUGHPUT)
      .value("SCAVENGER", QUIC_EXECUTION_PROFILE_TYPE_SCAVENGER)
      .value("REAL_TIME", QUIC_EXECUTION_PROFILE_TYPE_REAL_TIME);

  nb::enum_<QUIC_CONNECTION_SHUTDOWN_FLAGS>(m, "ConnectionShutdownFlags")
      .value("NONE", QUIC_CONNECTION_SHUTDOWN_FLAG_NONE)
      .value("SILENT", QUIC_CONNECTION_SHUTDOWN_FLAG_SILENT);

  nb::enum_<QUIC_STREAM_OPEN_FLAGS>(m, "StreamOpenFlags")
      .value("NONE", QUIC_STREAM_OPEN_FLAG_NONE)
      .value("UNIDIRECTIONAL", QUIC_STREAM_OPEN_FLAG_UNIDIRECTIONAL)
      .value("ZERO_RTT", QUIC_STREAM_OPEN_FLAG_0_RTT);

  nb::enum_<QUIC_STREAM_START_FLAGS>(m, "StreamStartFlags")
      .value("NONE", QUIC_STREAM_START_FLAG_NONE)
      .value("IMMEDIATE", QUIC_STREAM_START_FLAG_IMMEDIATE)
      .value("FAIL_BLOCKED", QUIC_STREAM_START_FLAG_FAIL_BLOCKED)
      .value("SHUTDOWN_ON_FAIL", QUIC_STREAM_START_FLAG_SHUTDOWN_ON_FAIL);

  nb::enum_<QUIC_STREAM_SHUTDOWN_FLAGS>(m, "StreamShutdownFlags")
      .value("NONE", QUIC_STREAM_SHUTDOWN_FLAG_NONE)
      .value("GRACEFUL", QUIC_STREAM_SHUTDOWN_FLAG_GRACEFUL)
      .value("ABORT_SEND", QUIC_STREAM_SHUTDOWN_FLAG_ABORT_SEND)
      .value("ABORT_RECEIVE", QUIC_STREAM_SHUTDOWN_FLAG_ABORT_RECEIVE)
      .value("ABORT", QUIC_STREAM_SHUTDOWN_FLAG_ABORT)
      .value("IMMEDIATE", QUIC_STREAM_SHUTDOWN_FLAG_IMMEDIATE);

  nb::enum_<QUIC_SEND_FLAGS>(m, "SendFlags")
      .value("NONE", QUIC_SEND_FLAG_NONE)
      .value("ALLOW_0_RTT", QUIC_SEND_FLAG_ALLOW_0_RTT)
      .value("START", QUIC_SEND_FLAG_START)
      .value("FIN", QUIC_SEND_FLAG_FIN)
      .value("DGRAM_PRIORITY", QUIC_SEND_FLAG_DGRAM_PRIORITY)
      .value("DELAY_SEND", QUIC_SEND_FLAG_DELAY_SEND);
}

// ========== Varint Functions ==========
// QUIC Variable-Length Integer Encoding (RFC 9000 Section 16)
// msquic の quic_var_int.h を使用

nb::bytes encode_varint(uint64_t value) {
  if (value > QUIC_VAR_INT_MAX) {
    throw std::overflow_error("Value too large for varint encoding");
  }

  uint8_t buffer[8];
  uint8_t* end = QuicVarIntEncode(value, buffer);
  size_t size = static_cast<size_t>(end - buffer);

  return nb::bytes(reinterpret_cast<char*>(buffer), size);
}

nb::tuple decode_varint(const nb::bytes& data, size_t offset = 0) {
  size_t buffer_length = data.size();

  if (offset >= buffer_length) {
    throw std::out_of_range("Offset is out of range");
  }

  const uint8_t* buffer = reinterpret_cast<const uint8_t*>(data.c_str());
  uint16_t pos = static_cast<uint16_t>(offset);
  QUIC_VAR_INT value;

  if (!QuicVarIntDecode(static_cast<uint16_t>(buffer_length), buffer, &pos,
                        &value)) {
    throw std::runtime_error("Insufficient data for varint decoding");
  }

  size_t consumed = static_cast<size_t>(pos - offset);
  return nb::make_tuple(value, consumed);
}

uint8_t varint_size(uint64_t value) {
  if (value > QUIC_VAR_INT_MAX) {
    throw std::overflow_error("Value too large for varint encoding");
  }
  return static_cast<uint8_t>(QuicVarIntSize(value));
}

}  // namespace msquic_py

void bind_msquic(nb::module_& m) {
  using namespace msquic_py;

  m.doc() = "Python bindings for MsQuic";

  // ユーティリティ関数
  m.def("open_api", &open_api, "Open the MsQuic API");
  m.def("close_api", &close_api, "Close the MsQuic API");

  // Varint 関数
  m.def("encode_varint", &encode_varint, "value"_a,
        "Encode an integer as a QUIC variable-length integer");
  m.def("decode_varint", &decode_varint, "data"_a, "offset"_a = 0,
        "Decode a QUIC variable-length integer, returns (value, consumed_bytes)");
  m.def("varint_size", &varint_size, "value"_a,
        "Get the number of bytes required to encode a value as varint");

  // Enums
  bind_enums(m);

  // Registration
  nb::class_<Registration>(m, "Registration")
      .def(nb::init<const std::string&, QUIC_EXECUTION_PROFILE>(),
           "app_name"_a, "profile"_a = QUIC_EXECUTION_PROFILE_LOW_LATENCY)
      .def("shutdown", &Registration::shutdown,
           "flags"_a = QUIC_CONNECTION_SHUTDOWN_FLAG_NONE, "error_code"_a = 0)
      .def("close", &Registration::close);

  // Configuration
  nb::class_<Configuration>(m, "Configuration")
      .def(nb::init<Registration&, const std::vector<std::string>&, uint64_t, uint16_t, uint16_t>(),
           "registration"_a, "alpn_list"_a, "idle_timeout_ms"_a = 0,
           "peer_bidi_stream_count"_a = 0, "peer_unidi_stream_count"_a = 0)
      .def("load_credential_file", &Configuration::load_credential_file,
           "cert_file"_a, "key_file"_a, "is_client"_a = false)
      .def("load_credential_none", &Configuration::load_credential_none,
           "no_certificate_validation"_a = false)
      .def("close", &Configuration::close);

  // Stream
  nb::class_<Stream>(m, "Stream")
      .def("start", &Stream::start, "flags"_a = QUIC_STREAM_START_FLAG_NONE)
      .def("send", &Stream::send, "data"_a, "flags"_a = QUIC_SEND_FLAG_NONE)
      .def("shutdown", &Stream::shutdown, "flags"_a, "error_code"_a = 0)
      .def("set_on_receive", &Stream::set_on_receive)
      .def("set_on_send_complete", &Stream::set_on_send_complete)
      .def("set_on_shutdown_complete", &Stream::set_on_shutdown_complete);

  // Connection
  nb::class_<Connection>(m, "Connection")
      .def(nb::init<Registration&>(), "registration"_a)
      .def("start", &Connection::start, "config"_a, "server_name"_a, "port"_a)
      .def("set_configuration", &Connection::set_configuration, "config"_a)
      .def("shutdown", &Connection::shutdown,
           "flags"_a = QUIC_CONNECTION_SHUTDOWN_FLAG_NONE, "error_code"_a = 0)
      .def("open_stream", &Connection::open_stream, "flags"_a = QUIC_STREAM_OPEN_FLAG_NONE)
      .def("set_on_connected", &Connection::set_on_connected)
      .def("set_on_shutdown_complete", &Connection::set_on_shutdown_complete)
      .def("set_on_peer_stream_started", &Connection::set_on_peer_stream_started);

  // Listener
  nb::class_<Listener>(m, "Listener")
      .def(nb::init<Registration&>(), "registration"_a)
      .def("start", &Listener::start, "config"_a, "alpn_list"_a, "port"_a)
      .def("stop", &Listener::stop)
      .def("close", &Listener::close)
      .def("set_on_new_connection", &Listener::set_on_new_connection);
}

// nanobind
#include <nanobind/nanobind.h>
#include <nanobind/stl/function.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/shared_ptr.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

// msquic
#include <msquic.h>

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
QUIC_STATUS QUIC_API StreamCallback(HQUIC stream, void* context, QUIC_STREAM_EVENT* event);

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
    if (handle_ != nullptr && g_MsQuic != nullptr) {
      g_MsQuic->RegistrationClose(handle_);
    }
  }

  HQUIC handle() const { return handle_; }

  void shutdown(QUIC_CONNECTION_SHUTDOWN_FLAGS flags, uint64_t error_code) {
    if (handle_ != nullptr && g_MsQuic != nullptr) {
      g_MsQuic->RegistrationShutdown(handle_, flags, error_code);
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
    if (handle_ != nullptr && g_MsQuic != nullptr) {
      g_MsQuic->ConfigurationClose(handle_);
    }
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

 private:
  HQUIC handle_ = nullptr;
};

// ========== Stream ==========
// Stream コールバック用のコンテキスト
struct StreamContext {
  std::function<void(const std::vector<uint8_t>&, bool)> on_receive;
  std::function<void()> on_send_complete;
  std::function<void(uint64_t)> on_peer_send_aborted;
  std::function<void(uint64_t)> on_peer_receive_aborted;
  std::function<void(bool)> on_shutdown_complete;
};

class Stream {
 public:
  Stream(HQUIC handle) : handle_(handle) {
    context_ = std::make_unique<StreamContext>();
    g_MsQuic->SetContext(handle_, context_.get());
  }

  ~Stream() {
    if (handle_ != nullptr && g_MsQuic != nullptr) {
      g_MsQuic->StreamClose(handle_);
    }
  }

  HQUIC handle() const { return handle_; }

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

    QUIC_STATUS status = g_MsQuic->StreamSend(handle_, buffer, 1, flags, buffer);
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
    context_->on_receive = std::move(callback);
  }

  void set_on_send_complete(std::function<void()> callback) {
    context_->on_send_complete = std::move(callback);
  }

  void set_on_shutdown_complete(std::function<void(bool)> callback) {
    context_->on_shutdown_complete = std::move(callback);
  }

 private:
  HQUIC handle_ = nullptr;
  std::unique_ptr<StreamContext> context_;
};

// Stream コールバック
QUIC_STATUS QUIC_API StreamCallback(HQUIC stream, void* context, QUIC_STREAM_EVENT* event) {
  auto* ctx = static_cast<StreamContext*>(context);

  switch (event->Type) {
    case QUIC_STREAM_EVENT_RECEIVE: {
      if (ctx && ctx->on_receive) {
        std::vector<uint8_t> data;
        for (uint32_t i = 0; i < event->RECEIVE.BufferCount; i++) {
          const auto& buf = event->RECEIVE.Buffers[i];
          data.insert(data.end(), buf.Buffer, buf.Buffer + buf.Length);
        }
        bool fin = (event->RECEIVE.Flags & QUIC_RECEIVE_FLAG_FIN) != 0;
        nb::gil_scoped_acquire acquire;
        ctx->on_receive(data, fin);
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
      if (ctx && ctx->on_send_complete) {
        nb::gil_scoped_acquire acquire;
        ctx->on_send_complete();
      }
      break;
    }
    case QUIC_STREAM_EVENT_PEER_SEND_ABORTED: {
      if (ctx && ctx->on_peer_send_aborted) {
        nb::gil_scoped_acquire acquire;
        ctx->on_peer_send_aborted(event->PEER_SEND_ABORTED.ErrorCode);
      }
      break;
    }
    case QUIC_STREAM_EVENT_PEER_RECEIVE_ABORTED: {
      if (ctx && ctx->on_peer_receive_aborted) {
        nb::gil_scoped_acquire acquire;
        ctx->on_peer_receive_aborted(event->PEER_RECEIVE_ABORTED.ErrorCode);
      }
      break;
    }
    case QUIC_STREAM_EVENT_SHUTDOWN_COMPLETE: {
      if (ctx && ctx->on_shutdown_complete) {
        nb::gil_scoped_acquire acquire;
        ctx->on_shutdown_complete(event->SHUTDOWN_COMPLETE.ConnectionShutdown);
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
  std::function<void()> on_connected;
  std::function<void(bool)> on_shutdown_complete;
  std::function<void(std::shared_ptr<Stream>)> on_peer_stream_started;
  std::vector<std::shared_ptr<Stream>> streams;
};

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
  }

  // サーバー側から受け入れた接続用
  Connection(HQUIC handle) : handle_(handle), registration_(nullptr) {
    context_ = std::make_unique<ConnectionContext>();
    g_MsQuic->SetContext(handle_, context_.get());
    g_MsQuic->SetCallbackHandler(handle_, (void*)ConnectionCallback, context_.get());
  }

  ~Connection() {
    if (handle_ != nullptr && g_MsQuic != nullptr) {
      g_MsQuic->ConnectionClose(handle_);
    }
  }

  HQUIC handle() const { return handle_; }

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
    g_MsQuic->SetCallbackHandler(stream_handle, (void*)StreamCallback, g_MsQuic->GetContext(stream_handle));
    context_->streams.push_back(stream);
    return stream;
  }

  void set_on_connected(std::function<void()> callback) {
    context_->on_connected = std::move(callback);
  }

  void set_on_shutdown_complete(std::function<void(bool)> callback) {
    context_->on_shutdown_complete = std::move(callback);
  }

  void set_on_peer_stream_started(std::function<void(std::shared_ptr<Stream>)> callback) {
    context_->on_peer_stream_started = std::move(callback);
  }

 private:
  HQUIC handle_ = nullptr;
  Registration* registration_;
  std::unique_ptr<ConnectionContext> context_;

  static QUIC_STATUS QUIC_API ConnectionCallback(HQUIC connection, void* context, QUIC_CONNECTION_EVENT* event);
};

QUIC_STATUS QUIC_API Connection::ConnectionCallback(HQUIC connection, void* context, QUIC_CONNECTION_EVENT* event) {
  auto* ctx = static_cast<ConnectionContext*>(context);

  switch (event->Type) {
    case QUIC_CONNECTION_EVENT_CONNECTED: {
      if (ctx && ctx->on_connected) {
        nb::gil_scoped_acquire acquire;
        ctx->on_connected();
      }
      break;
    }
    case QUIC_CONNECTION_EVENT_SHUTDOWN_COMPLETE: {
      if (ctx && ctx->on_shutdown_complete) {
        nb::gil_scoped_acquire acquire;
        ctx->on_shutdown_complete(event->SHUTDOWN_COMPLETE.AppCloseInProgress);
      }
      break;
    }
    case QUIC_CONNECTION_EVENT_PEER_STREAM_STARTED: {
      if (ctx && ctx->on_peer_stream_started) {
        auto stream = std::make_shared<Stream>(event->PEER_STREAM_STARTED.Stream);
        ctx->streams.push_back(stream);
        g_MsQuic->SetCallbackHandler(event->PEER_STREAM_STARTED.Stream, (void*)StreamCallback, g_MsQuic->GetContext(stream->handle()));
        nb::gil_scoped_acquire acquire;
        ctx->on_peer_stream_started(stream);
      }
      break;
    }
    default:
      break;
  }
  return QUIC_STATUS_SUCCESS;
}

// ========== Listener ==========
struct ListenerContext {
  std::function<void(std::shared_ptr<Connection>)> on_new_connection;
  Configuration* config;
  std::vector<std::shared_ptr<Connection>> connections;
};

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
  }

  ~Listener() {
    if (handle_ != nullptr && g_MsQuic != nullptr) {
      g_MsQuic->ListenerClose(handle_);
    }
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
    g_MsQuic->ListenerStop(handle_);
  }

  void set_on_new_connection(std::function<void(std::shared_ptr<Connection>)> callback) {
    context_->on_new_connection = std::move(callback);
  }

 private:
  HQUIC handle_ = nullptr;
  std::unique_ptr<ListenerContext> context_;
  std::vector<QUIC_BUFFER> alpn_buffers_;

  static QUIC_STATUS QUIC_API ListenerCallback(HQUIC listener, void* context, QUIC_LISTENER_EVENT* event);
};

QUIC_STATUS QUIC_API Listener::ListenerCallback(HQUIC listener, void* context, QUIC_LISTENER_EVENT* event) {
  auto* ctx = static_cast<ListenerContext*>(context);

  switch (event->Type) {
    case QUIC_LISTENER_EVENT_NEW_CONNECTION: {
      auto connection = std::make_shared<Connection>(event->NEW_CONNECTION.Connection);
      ctx->connections.push_back(connection);

      // Configuration を設定
      if (ctx->config) {
        QUIC_STATUS status = g_MsQuic->ConnectionSetConfiguration(
            event->NEW_CONNECTION.Connection,
            ctx->config->handle());
        if (QUIC_FAILED(status)) {
          return status;
        }
      }

      if (ctx->on_new_connection) {
        nb::gil_scoped_acquire acquire;
        ctx->on_new_connection(connection);
      }
      break;
    }
    case QUIC_LISTENER_EVENT_STOP_COMPLETE:
      break;
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

}  // namespace msquic_py

void bind_msquic(nb::module_& m) {
  using namespace msquic_py;

  m.doc() = "Python bindings for MsQuic";

  // ユーティリティ関数
  m.def("open_api", &open_api, "Open the MsQuic API");
  m.def("close_api", &close_api, "Close the MsQuic API");

  // Enums
  bind_enums(m);

  // Registration
  nb::class_<Registration>(m, "Registration")
      .def(nb::init<const std::string&, QUIC_EXECUTION_PROFILE>(),
           "app_name"_a, "profile"_a = QUIC_EXECUTION_PROFILE_LOW_LATENCY)
      .def("shutdown", &Registration::shutdown,
           "flags"_a = QUIC_CONNECTION_SHUTDOWN_FLAG_NONE, "error_code"_a = 0);

  // Configuration
  nb::class_<Configuration>(m, "Configuration")
      .def(nb::init<Registration&, const std::vector<std::string>&, uint64_t, uint16_t, uint16_t>(),
           "registration"_a, "alpn_list"_a, "idle_timeout_ms"_a = 0,
           "peer_bidi_stream_count"_a = 0, "peer_unidi_stream_count"_a = 0)
      .def("load_credential_file", &Configuration::load_credential_file,
           "cert_file"_a, "key_file"_a, "is_client"_a = false)
      .def("load_credential_none", &Configuration::load_credential_none,
           "no_certificate_validation"_a = false);

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
      .def("set_on_new_connection", &Listener::set_on_new_connection);
}

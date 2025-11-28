cmake_minimum_required(VERSION 4.1)
project(msquic_py LANGUAGES C CXX)

# プラットフォームチェック
if(NOT APPLE AND NOT UNIX AND NOT WIN32)
  message(FATAL_ERROR "This package supports macOS, Linux (Ubuntu), and Windows.")
endif()

# ExternalProject の依存関係は _deps 配下で管理
if(DEFINED SKBUILD_PROJECT_DIR)
  set(PROJECT_ROOT "${SKBUILD_PROJECT_DIR}")
else()
  set(PROJECT_ROOT "${CMAKE_CURRENT_SOURCE_DIR}")
endif()

set(DEPS_DIR "${PROJECT_ROOT}/_deps")
message(STATUS "Dependencies directory: ${DEPS_DIR}")

# Python / nanobind
find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)
find_package(nanobind CONFIG REQUIRED)

# 依存関係用の ExternalProject
include(ExternalProject)

# JSON から依存関係のバージョンを読み込み
file(READ ${CMAKE_CURRENT_SOURCE_DIR}/deps.json DEPS_JSON)

# msquic の設定を解析
string(JSON MSQUIC_VERSION GET ${DEPS_JSON} msquic version)
string(JSON MSQUIC_GIT_REPOSITORY GET ${DEPS_JSON} msquic repository)
set(MSQUIC_GIT_TAG "v${MSQUIC_VERSION}")

# ExternalProject ビルド用の並列ビルドジョブ数を決定
if(DEFINED ENV{CMAKE_BUILD_PARALLEL_LEVEL})
  set(PARALLEL_JOBS "$ENV{CMAKE_BUILD_PARALLEL_LEVEL}")
else()
  include(ProcessorCount)
  ProcessorCount(NCORES)
  if(NOT NCORES OR NCORES EQUAL 0)
    set(NCORES 2)
  endif()
  set(PARALLEL_JOBS "${NCORES}")
endif()

# ========== msquic ==========
message(STATUS "Setting up msquic...")
set(MSQUIC_SOURCE_DIR "${DEPS_DIR}/msquic/${MSQUIC_VERSION}/source")
set(MSQUIC_BUILD_DIR "${DEPS_DIR}/msquic/${MSQUIC_VERSION}/build")
set(MSQUIC_INSTALL_DIR "${DEPS_DIR}/msquic/${MSQUIC_VERSION}/install")

# 静的ライブラリパス設定
if(WIN32)
  set(MSQUIC_LIB ${MSQUIC_INSTALL_DIR}/lib/msquic.lib)
elseif(APPLE)
  set(MSQUIC_LIB ${MSQUIC_INSTALL_DIR}/lib/libmsquic.a)
else()
  set(MSQUIC_LIB ${MSQUIC_INSTALL_DIR}/lib/libmsquic.a)
endif()

# ビルド済みかチェック
if(EXISTS ${MSQUIC_LIB})
  message(STATUS "msquic already built: ${MSQUIC_LIB}")
  add_custom_target(msquic_build)
else()
  message(STATUS "Building msquic...")

  # プラットフォーム共通の CMake 引数
  set(MSQUIC_CMAKE_ARGS
    -DCMAKE_INSTALL_PREFIX=${MSQUIC_INSTALL_DIR}
    -DCMAKE_BUILD_TYPE=Release
    -DQUIC_BUILD_TOOLS=OFF
    -DQUIC_BUILD_TEST=OFF
    -DQUIC_BUILD_PERF=OFF
    -DQUIC_BUILD_SHARED=OFF
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
  )

  # プラットフォーム固有の TLS 設定
  # Windows: schannel (Windows 組み込み、OpenSSL 不要)
  # macOS/Linux: quictls を静的リンク
  if(WIN32)
    set(MSQUIC_CMAKE_CACHE_ARGS
      -DQUIC_TLS_LIB:STRING=schannel
    )
  else()
    set(MSQUIC_CMAKE_CACHE_ARGS
      -DQUIC_TLS_LIB:STRING=quictls
      -DQUIC_USE_SYSTEM_LIBCRYPTO:BOOL=OFF
    )
  endif()

  ExternalProject_Add(
    msquic_build
    GIT_REPOSITORY ${MSQUIC_GIT_REPOSITORY}
    GIT_TAG ${MSQUIC_GIT_TAG}
    GIT_SHALLOW TRUE
    GIT_SUBMODULES_RECURSE TRUE
    SOURCE_DIR ${MSQUIC_SOURCE_DIR}
    BINARY_DIR ${MSQUIC_BUILD_DIR}
    STAMP_DIR ${MSQUIC_BUILD_DIR}/stamp
    TMP_DIR ${MSQUIC_BUILD_DIR}/tmp
    UPDATE_COMMAND ""
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CMAKE_ARGS ${MSQUIC_CMAKE_ARGS}
    CMAKE_CACHE_ARGS ${MSQUIC_CMAKE_CACHE_ARGS}
    BUILD_COMMAND ${CMAKE_COMMAND} --build ${MSQUIC_BUILD_DIR} --config Release --parallel ${PARALLEL_JOBS}
    INSTALL_COMMAND ${CMAKE_COMMAND} --build ${MSQUIC_BUILD_DIR} --config Release --target install
    BUILD_BYPRODUCTS ${MSQUIC_LIB}
  )
endif()

# nanobind モジュール
nanobind_add_module(msquic_ext
  NB_DOMAIN "msquic"
  src/bind_msquic.cpp
  src/msquic_ext.cpp
)

# ビルド順序を保証するため依存関係を追加
add_dependencies(msquic_ext msquic_build)

# インクルードディレクトリ
target_include_directories(msquic_ext PRIVATE
  ${MSQUIC_INSTALL_DIR}/include
)

# ライブラリをリンク
target_link_libraries(msquic_ext PRIVATE ${MSQUIC_LIB})

# プラットフォーム固有の設定
if(APPLE)
  target_link_libraries(msquic_ext PRIVATE
    "-framework CoreFoundation"
    "-framework Security"
  )
elseif(WIN32)
  # 文字コードを utf-8 として扱うのと、シンボルテーブル数を増やす
  target_compile_options(msquic_ext PRIVATE /utf-8 /bigobj)
  # CRT ライブラリを静的リンクさせる
  set_property(TARGET msquic_ext PROPERTY
    MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
  set_property(TARGET nanobind-static PROPERTY
    MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
  target_compile_definitions(msquic_ext PRIVATE
    _CONSOLE
    _WIN32_WINNT=0x0A00
    NOMINMAX
    WIN32_LEAN_AND_MEAN
  )
endif()

# コンパイルオプション
if(NOT WIN32)
  target_compile_options(msquic_ext PRIVATE -fvisibility=hidden)
endif()

set_target_properties(msquic_ext PROPERTIES OUTPUT_NAME "msquic_ext")

# 型スタブ (.pyi) ファイルの自動生成
nanobind_add_stub(
  msquic_stub
  MODULE msquic_ext
  OUTPUT __init__.pyi
  PYTHON_PATH $<TARGET_FILE_DIR:msquic_ext>
  DEPENDS msquic_ext
  MARKER_FILE py.typed
)

# Wheel に配置するためのインストール先を指定
set(PY_PACKAGE "msquic")
install(TARGETS msquic_ext
  LIBRARY DESTINATION ${PY_PACKAGE}
  RUNTIME DESTINATION ${PY_PACKAGE}
)

# 型スタブファイルとマーカーファイルをインストール
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/__init__.pyi DESTINATION ${PY_PACKAGE})
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/py.typed DESTINATION ${PY_PACKAGE})

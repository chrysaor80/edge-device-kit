cmake_minimum_required(VERSION 3.20)
project(edgekit CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

option(EDGEKIT_SANITIZE "Build with AddressSanitizer + UndefinedBehaviorSanitizer" OFF)
option(EDGEKIT_FUZZ "Build the libFuzzer target (requires clang)" OFF)
option(EDGEKIT_TESTS "Build unit tests (fetches GoogleTest)" ON)

set(EDGEKIT_WARNINGS -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Werror)

if(EDGEKIT_SANITIZE)
  add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all)
  add_link_options(-fsanitize=address,undefined)
endif()

add_library(edgekit
  src/crc.cpp src/modbus.cpp src/lenframe.cpp src/sensirion.cpp src/transport.cpp)
target_include_directories(edgekit PUBLIC include)
target_compile_options(edgekit PRIVATE ${EDGEKIT_WARNINGS})
set_target_properties(edgekit PROPERTIES POSITION_INDEPENDENT_CODE ON)

# Shared library with a flat C interface, loaded by tests_cross/ via ctypes.
add_library(edgekit_c SHARED src/c_api.cpp)
target_link_libraries(edgekit_c PRIVATE edgekit)
target_compile_options(edgekit_c PRIVATE ${EDGEKIT_WARNINGS})

add_executable(edgekit_poll tools/edgekit_poll.cpp)
target_link_libraries(edgekit_poll PRIVATE edgekit)
target_compile_options(edgekit_poll PRIVATE ${EDGEKIT_WARNINGS})

if(EDGEKIT_TESTS)
  include(FetchContent)
  FetchContent_Declare(googletest
    URL https://github.com/google/googletest/archive/refs/tags/v1.15.2.tar.gz
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
  set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
  FetchContent_MakeAvailable(googletest)
  enable_testing()
  add_executable(edgekit_tests tests/test_codecs.cpp tests/test_reader.cpp)
  target_link_libraries(edgekit_tests PRIVATE edgekit GTest::gtest_main)
  include(GoogleTest)
  gtest_discover_tests(edgekit_tests)

  # Same fuzz invariants without libFuzzer (any compiler); run with EDGEKIT_SANITIZE=ON.
  add_executable(fuzz_smoke fuzz/standalone_main.cpp fuzz/fuzz_parsers.cpp)
  target_link_libraries(fuzz_smoke PRIVATE edgekit)
  add_test(NAME fuzz_smoke COMMAND fuzz_smoke 100000)
endif()

if(EDGEKIT_FUZZ)
  add_executable(fuzz_parsers fuzz/fuzz_parsers.cpp
    src/crc.cpp src/modbus.cpp src/lenframe.cpp src/sensirion.cpp)
  target_include_directories(fuzz_parsers PRIVATE include)
  target_compile_options(fuzz_parsers PRIVATE -fsanitize=fuzzer,address,undefined -g -O1)
  target_link_options(fuzz_parsers PRIVATE -fsanitize=fuzzer,address,undefined)
endif()

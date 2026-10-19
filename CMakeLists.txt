cmake_minimum_required(VERSION 3.16)
project(CppHomeworkJudge LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Debug CACHE STRING "构建类型" FORCE)
endif()

add_executable(judge
    src/main.cpp
    src/status.cpp
    src/text.cpp
    src/runner.cpp
    src/compiler.cpp
    src/report.cpp
)
target_include_directories(judge PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
find_package(Threads REQUIRED)
target_link_libraries(judge PRIVATE Threads::Threads)
if(MSVC)
    target_compile_options(judge PRIVATE /W3 /utf-8)
else()
    target_compile_options(judge PRIVATE -Wall -Wextra)
endif()

# forkpty/openpty 在部分系统里位于 libutil（仅类 Unix 平台需要）
if(UNIX)
    find_library(UTIL_LIB util)
    if(UTIL_LIB)
        target_link_libraries(judge PRIVATE ${UTIL_LIB})
    endif()
endif()

# 便捷目标：cmake --build build --target judge-run
add_custom_target(judge-run
    COMMAND $<TARGET_FILE:judge> --root ${CMAKE_CURRENT_SOURCE_DIR}
    WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
    DEPENDS judge
    USES_TERMINAL
    COMMENT "运行判题器（项目根目录 ${CMAKE_CURRENT_SOURCE_DIR}）"
)

# 列出题目与用例，不判题
add_custom_target(judge-list
    COMMAND $<TARGET_FILE:judge> --root ${CMAKE_CURRENT_SOURCE_DIR} --list
    WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
    DEPENDS judge
    USES_TERMINAL
    COMMENT "列出所有题目与测试用例"
)

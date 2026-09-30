cmake_minimum_required(VERSION 3.8)
project(gripper_cmd_skill)

find_package(ament_cmake REQUIRED)
find_package(intrinsic_sdk_cmake REQUIRED)
find_package(zenoh_cpp_vendor REQUIRED)
find_package(zenohcxx REQUIRED)

intrinsic_sdk_protobuf_generate(
  NAME gripper_cmd_skill
  SOURCES gripper_cmd_skill.proto
  TARGET gripper_cmd_skill_protos
  DESCRIPTOR_SET_OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/gripper_cmd_skill.desc"
)

intrinsic_sdk_generate_skill_config(
  TARGET gripper_cmd_skill_skill_config
  SKILL_NAME gripper_cmd_skill
  MANIFEST gripper_cmd_skill.manifest.textproto
  PROTO_DESCRIPTOR_FILE "${CMAKE_CURRENT_BINARY_DIR}/gripper_cmd_skill.desc"
  SKILL_CONFIG_FILE_OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/gripper_cmd_skill_config.pbbin"
)
install(
  FILES
    "${CMAKE_CURRENT_BINARY_DIR}/gripper_cmd_skill_config.pbbin"
    gripper_cmd_skill.manifest.textproto
  DESTINATION share/${PROJECT_NAME}
)
install(
  FILES "${CMAKE_CURRENT_BINARY_DIR}/gripper_cmd_skill.desc"
  DESTINATION share/${PROJECT_NAME}
  RENAME gripper_cmd_skill_protos.desc
)

intrinsic_sdk_generate_skill_main_cc(
  MANIFEST gripper_cmd_skill.manifest.textproto
  HEADER_FILES gripper_cmd_skill.h
  MAIN_FILE_OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/gripper_cmd_skill_main.cc"
  FILE_DESCRIPTOR_SET "${CMAKE_CURRENT_BINARY_DIR}/gripper_cmd_skill.desc"
)

add_executable(gripper_cmd_skill_main
  "${CMAKE_CURRENT_BINARY_DIR}/gripper_cmd_skill_main.cc"
  gripper_cmd_skill.cc
)
target_include_directories(gripper_cmd_skill_main
  PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}"
)
target_link_libraries(gripper_cmd_skill_main
  intrinsic_sdk_cmake::intrinsic_sdk_cmake
  gripper_cmd_skill_protos
  zenohcxx::zenohc
)
install(
  TARGETS gripper_cmd_skill_main
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()

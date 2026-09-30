# Native build of the gripper_cmd_skill image (hand_e_gripper_cmd_skill bundle)
# on top of the sdk-ros underlay (localhost/sdk-ros-underlay:lyrical, built from
# intrinsic-ai/sdk-ros's service.Dockerfile with --target underlay).
# Build from the repository root:
#   podman build -f image/skill.Containerfile .
ARG ROS_DISTRO=lyrical
ARG UNDERLAY=localhost/sdk-ros-underlay:lyrical

FROM ${UNDERLAY} AS build
ARG ROS_DISTRO
ENV SKILL_WORKSPACE=/opt/gripper_cmd_skill_workspace
COPY gripper_cmd_skill ${SKILL_WORKSPACE}/src/gripper_cmd_skill
RUN . /opt/ros/${ROS_DISTRO}/setup.sh \
    && . /opt/ros/underlay/install/setup.sh \
    && apt-get update \
    && cd ${SKILL_WORKSPACE} \
    && rosdep install --from-paths src /opt/ros/underlay/src --ignore-src -r -y \
         --dependency-types exec \
    && dpkg --get-selections > /skill_exec_apt_packages.txt \
    && rosdep install --from-paths src --ignore-src -r -y \
    && colcon build --merge-install --cmake-args -DCMAKE_BUILD_TYPE=Release \
         --packages-select gripper_cmd_skill \
    && rm -rf /var/lib/apt/lists/*

FROM docker.io/library/ros:${ROS_DISTRO}
ARG ROS_DISTRO
ENV SKILL_WORKSPACE=/opt/gripper_cmd_skill_workspace
ENV RMW_IMPLEMENTATION=rmw_zenoh_cpp
COPY --from=build /skill_exec_apt_packages.txt /skill_exec_apt_packages.txt
RUN apt-get update \
    && apt-cache dumpavail | dpkg --merge-avail \
    && dpkg --set-selections < /skill_exec_apt_packages.txt \
    && apt-get dselect-upgrade -y \
    && apt-get install -y --no-install-recommends ros-${ROS_DISTRO}-zenoh-cpp-vendor \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/ros/underlay/install /opt/ros/underlay/install
COPY --from=build ${SKILL_WORKSPACE}/install ${SKILL_WORKSPACE}/install
ENV SKILL_EXECUTABLE_ABS=${SKILL_WORKSPACE}/install/lib/gripper_cmd_skill/gripper_cmd_skill_main
ENV SKILL_CONFIG_ABS=${SKILL_WORKSPACE}/install/share/gripper_cmd_skill/gripper_cmd_skill_config.pbbin
RUN mkdir -p /skills \
    && ln -sf ${SKILL_EXECUTABLE_ABS} /skills/skill_service \
    && ln -sf ${SKILL_CONFIG_ABS} /skills/skill_service_config.proto.bin \
    && sed --in-place \
        --expression '$isource "/opt/ros/underlay/install/setup.bash"' \
        --expression '$isource "$SKILL_WORKSPACE/install/setup.bash"' \
        /ros_entrypoint.sh
COPY --chmod=755 image/skill_entrypoint.py /workspace/skill_entrypoint.py
WORKDIR /workspace
LABEL "ai.intrinsic.asset-id"="ai.intrinsic.gripper_cmd_skill"
LABEL "ai.intrinsic.skill-image-name"="gripper_cmd_skill"
ENTRYPOINT ["/usr/bin/python3", "/workspace/skill_entrypoint.py"]
CMD ["/skills/skill_service", "--skill_service_config_filename=/skills/skill_service_config.proto.bin"]

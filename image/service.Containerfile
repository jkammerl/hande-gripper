# Native build of the Robotiq Hand-E gripper service images (hand_e_gripper_service
# bundle), in the layout of the original images:
#   VARIANT=real: gateway + ros2_control driver (AGH-CEAI/robotiq_hande_driver)
#   VARIANT=sim:  gateway + simulation driver
# Build from the repository root:
#   podman build -f image/service.Containerfile --build-arg VARIANT=sim .
ARG ROS_DISTRO=lyrical

FROM docker.io/library/ros:${ROS_DISTRO} AS build
ARG ROS_DISTRO
ARG VARIANT
# The Intrinsic SDK release whose protos the simulation driver uses (the one
# intrinsic-ai/sdk-ros pins).
ARG SDK_VERSION=v1.36.20260831
ARG SDK_SHA256=96dc4b9b7bc33a65ba33461d7ad1e5471db0745c89f5d30df1c69ffaf50f3f3a
ARG ROBOTIQ_HANDE_TAG=v0.2.0-jazzy

RUN apt-get update && apt-get dist-upgrade -y \
    && apt-get install -y --no-install-recommends \
       git patch curl python3-pip python3-colcon-common-extensions \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --break-system-packages --no-cache-dir \
      grpcio-tools==1.78.0 googleapis-common-protos==1.72.0

WORKDIR /workspace
COPY aquarium_gateway src/aquarium_gateway
COPY hande_gripper_sim_driver src/hande_gripper_sim_driver
COPY hande_gripper_aquarium src/hande_gripper_aquarium
COPY image/patches /tmp/patches
RUN if [ "${VARIANT}" = real ]; then \
      git clone --depth 1 -b ${ROBOTIQ_HANDE_TAG} https://github.com/AGH-CEAI/robotiq_hande_driver src/robotiq_hande_driver \
      && git clone --depth 1 -b ${ROBOTIQ_HANDE_TAG} https://github.com/AGH-CEAI/robotiq_hande_description src/robotiq_hande_description \
      && for p in /tmp/patches/robotiq_hande_driver_*.patch; do patch -d src/robotiq_hande_driver -p1 < $p; done; \
    elif [ "${VARIANT}" != sim ]; then echo "VARIANT must be real or sim" >&2; exit 1; fi

# Protos: the simulation driver's descriptor set and the entrypoint's config protos.
COPY image/hande_gripper_aquarium_hande_gripper_launch_xml_config.proto /workspace/config_protos_src/
RUN curl -sSL -o /tmp/sdk.tgz https://github.com/intrinsic-ai/sdk/archive/refs/tags/${SDK_VERSION}.tar.gz \
    && echo "${SDK_SHA256}  /tmp/sdk.tgz" | sha256sum -c \
    && mkdir /tmp/sdk && tar -xzf /tmp/sdk.tgz -C /tmp/sdk --strip-components=1 \
    && GOOGLEAPIS=$(python3 -c "import google.api.annotations_pb2 as a, os; print(os.path.dirname(os.path.dirname(os.path.dirname(a.__file__))))") \
    && WKT=$(python3 -c "import grpc_tools, os; print(os.path.join(os.path.dirname(grpc_tools.__file__), '_proto'))") \
    && cd /tmp/sdk \
    && python3 -m grpc_tools.protoc -I. -I${GOOGLEAPIS} -I${WKT} --include_imports \
         --descriptor_set_out=/workspace/src/hande_gripper_sim_driver/intrinsic_protos.desc \
         intrinsic/platform/pubsub/adapters/pubsub.proto \
         intrinsic/hardware/gripper/gripper.proto \
         intrinsic/world/proto/object_world_service.proto \
    && mkdir -p /workspace/config_protos \
    && python3 -m grpc_tools.protoc -Iintrinsic/resources/proto -I${WKT} \
         --python_out=/workspace/config_protos runtime_context.proto \
    && python3 -m grpc_tools.protoc -I/workspace/config_protos_src \
         --python_out=/workspace/config_protos \
         hande_gripper_aquarium_hande_gripper_launch_xml_config.proto \
    && rm -rf /tmp/sdk /tmp/sdk.tgz

RUN . /opt/ros/${ROS_DISTRO}/setup.sh \
    && if [ "${VARIANT}" = real ]; then \
         PKGS="aquarium_gateway hande_gripper_aquarium robotiq_hande_description robotiq_hande_driver"; \
       else PKGS="aquarium_gateway hande_gripper_sim_driver"; fi \
    && echo ${PKGS} | tr ' ' '\n' > /workspace/target_packages.txt \
    && apt-get update \
    && rosdep update \
    && rosdep install --from-paths src --ignore-src -r -y \
         --skip-keys "urdf_launch ros2_control_test_assets" \
    && colcon build --merge-install --install-base /workspace/install \
         --packages-up-to ${PKGS} --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
    && rm -rf /var/lib/apt/lists/*

FROM docker.io/library/ros:${ROS_DISTRO}
ARG ROS_DISTRO
ARG VARIANT
SHELL ["/bin/bash", "-c"]
ENV RMW_IMPLEMENTATION=rmw_zenoh_cpp
WORKDIR /workspace
COPY --from=build /workspace/src /tmp/src
COPY --from=build /workspace/target_packages.txt /workspace/target_packages.txt
RUN apt-get update && apt-get dist-upgrade -y \
    && apt-get install -y --no-install-recommends python3-pip python3-rosdep \
       ros-${ROS_DISTRO}-rmw-zenoh-cpp \
    && (rosdep init || true) && rosdep update \
    && rosdep install --from-paths $(sed 's|^|/tmp/src/|' /workspace/target_packages.txt) \
         --ignore-src -r -y --dependency-types exec \
         --skip-keys "urdf_launch ros2_control_test_assets" \
    && pip install --break-system-packages --no-cache-dir eclipse-zenoh==1.7.2 \
    && rm -rf /var/lib/apt/lists/* /tmp/src
COPY --from=build /workspace/install /workspace/install
COPY --from=build /workspace/config_protos /workspace/config_protos
COPY hande_gripper_aquarium/config/hande_gripper_aquarium.yaml /workspace/aquarium.yaml
COPY image/aquarium_launch_${VARIANT}.xml /workspace/aquarium_launch.xml
COPY --chmod=755 image/entrypoint.py image/entrypoint.sh /workspace/
ENTRYPOINT ["/workspace/entrypoint.sh"]

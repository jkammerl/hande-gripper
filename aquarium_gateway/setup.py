from setuptools import find_packages, setup

package_name = "aquarium_gateway"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages",
         ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Julius Kammerl",
    maintainer_email="julius@kammerl.de",
    description="Zenoh/gRPC protobuf to ROS 2 action gateway",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "aquarium_gateway = aquarium_gateway.gateway:main",
        ],
    },
)

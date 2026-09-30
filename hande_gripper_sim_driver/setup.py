import glob

from setuptools import find_packages, setup

package_name = "hande_gripper_sim_driver"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages",
         ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob.glob("launch/*.xml")),
        # Generated at image build time from the Intrinsic SDK's protos.
        ("share/" + package_name, glob.glob("intrinsic_protos.desc")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Julius Kammerl",
    maintainer_email="julius@kammerl.de",
    description="Simulated Robotiq Hand-E driver",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "hande_gripper_sim_driver_node = hande_gripper_sim_driver.node:main",
        ],
    },
)

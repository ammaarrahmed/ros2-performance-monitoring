from dataclasses import dataclass

@dataclass(frozen=True)
class RunDefaults:
 container_repo_url: str = "https://github.com/skyegalaxy/ros2-benchmark-container"
 container_ref : str="skyegalaxy/combined-fixes"
 ros_distro: str="lyrical"
 executor: str="single-threaded"
 duration: int=60
 cache_dir: str="~/.cache/ros2-performance-monitoring"
 results_dir: str="./results"



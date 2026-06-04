import argparse
import sys
from .config import RunDefaults

def run_command(args):
    print("Running Performance Monitor...")

def doctor_command(args):
    print("Checking environment...")


def main():
    defaults=RunDefaults()
    parser=argparse.ArgumentParser(prog="ros2-performance-monitoring")
    subparsers=parser.add_subparsers(dest="command",required=True)

    run_parser=subparsers.add_parser("run",help="Start monitoring")
    run_parser.set_defaults(func=run_command)
   

    doctor_parser=subparsers.add_parser("doctor",help="Check setup")
    doctor_parser.set_defaults(func=doctor_command)


    run_parser.add_argument(
        "duration",nargs="?",type=int,default=defaults.duration,
        help="Duration in Seconds"
        )
    run_parser.add_argument(
        "ros_distro",nargs="?",default=defaults.ros_distro,
        help="ROS Distro"
        )
    run_parser.add_argument(
        "executor",nargs="?",default=defaults.executor,
        help="Executor"
        )
    run_parser.add_argument(
        "results-dir",nargs="?",default=defaults.results_dir,
        help="Results directory for Container Run Results"
        )
    run_parser.add_argument(
        "cache_dir",nargs="?",default=defaults.cache_dir,
        help="Cache Directory for Container repo"
        )
    run_parser.add_argument(
        "container_repo_url",nargs="?", default=defaults.container_repo_url,
        help="Container Repo URL"
        )
    run_parser.add_argument(
        "container_ref",nargs="?", default=defaults.container_ref,
        help="Container Repository Ref"
        )
    args=parser.parse_args()
    return args.func(args)

if __name__=="__main__":
    sys.exit(main())

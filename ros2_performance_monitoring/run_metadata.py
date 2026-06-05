from datetime import datetime,timezone
import platform
import sys
import json
from pathlib import Path



def generation_rundata(args,results_dir,commit_hash):
    run_timestamp=datetime.now(timezone.utc)
    file_timestamp=run_timestamp.strftime("%Y%m%d_%H%M%S")
    iso_format=run_timestamp.isoformat()
    py_ver=sys.version.split()[0]
    machine=platform.machine()
    OS=platform.system()
    run_data={
        "host_environment":{
            "timestamp " :iso_format,
            "Python version": py_ver,
            "architecture" : machine,
            "OS": OS
        },

        "run_configuration":{
            "ros_distro":args.ros_distro,
            "executor":args.executor,
            "duration":args.duration,
        },

        "target_repo":{
            "url":args.container_repo_url,
            "ref":args.container_ref,
            "resolved_commit_hash":commit_hash
        }
    }

    output_dir=Path(results_dir).expanduser().resolve()
    output_dir.mkdir(parents=True,exist_ok=True)
    metadata_file=output_dir /f"metadata_{file_timestamp}.json"
    with open(metadata_file,"w") as f:
        json.dump(run_data,f,indent=4)
    print(f"Run metadata saved to : {output_dir} / {metadata_file}")

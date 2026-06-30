from pathlib import Path
import json
import subprocess
from typing import Optional, Tuple

CONTAINER_REPOS_FILE: Path=Path(__file__).with_name("ros2_benchmark_container.repos")

def get_default_container_repo() -> Tuple[Optional[str], Optional[str]]:
    repo_url: Optional[str]=None
    repo_version: Optional[str]=None
    with open(CONTAINER_REPOS_FILE) as f:
        for line in f:
            stripped_line=line.strip()
            if(stripped_line.startswith("url:")):
                repo_url=stripped_line.split(":",1)[1].strip()
            if(stripped_line.startswith("version:")):
                repo_version=stripped_line.split(":",1)[1].strip()
    return repo_url,repo_version

def update_existing_cache_remote(absolute_path: Path,container_repo_url: str) -> None:
    git_folder=absolute_path / ".git"
    if(not git_folder.is_dir()):
        return
    result=subprocess.run(
        ["git","remote","get-url","origin"],
        capture_output=True,
        cwd=absolute_path,
        text=True,
    )
    if(result.returncode != 0):
        subprocess.run(["git","remote","add","origin",container_repo_url],cwd=absolute_path,check=True)
    elif(result.stdout.strip() != container_repo_url):
        subprocess.run(["git","remote","set-url","origin",container_repo_url],cwd=absolute_path,check=True)

def setup_container_repo(container_repo_url: str, container_ref: str,cache_dir: str) -> str:
    relative_path=Path(cache_dir)
    absolute_path=relative_path.expanduser().resolve()
    absolute_path.mkdir(parents=True,exist_ok=True)
    update_existing_cache_remote(absolute_path,container_repo_url)
    default_container_repo_url,default_container_ref=get_default_container_repo()
    if(
        container_repo_url == default_container_repo_url
        and container_ref == default_container_ref
    ):
        subprocess.run(
            ["vcs","import","--recursive","--input",str(CONTAINER_REPOS_FILE),str(absolute_path)],
            check=True,
        )
    else:
        repos_manifest=json.dumps({
            "repositories": {
                ".": {
                    "type": "git",
                    "url": container_repo_url,
                    "version": container_ref,
                }
            }
        })
        subprocess.run(
            ["vcs","import","--recursive","--input","-",str(absolute_path)],
            input=repos_manifest,
            text=True,
            check=True,
        )
    result=subprocess.run(
        ["git","rev-parse","HEAD"],
        capture_output=True,
        cwd=absolute_path,
        text=True,
        check=True
    )
    commit_hash=result.stdout.strip()
    print(f"Resolved Commit : {commit_hash}")
    return commit_hash
        

    


       
    

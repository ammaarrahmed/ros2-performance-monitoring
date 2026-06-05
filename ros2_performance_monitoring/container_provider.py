from pathlib import Path
import subprocess

def setup_container_repo(container_repo_url, container_ref,cache_dir):
    relative_path=Path(cache_dir)
    absolute_path=relative_path.expanduser().resolve()
    git_folder=absolute_path / ".git"
    if(git_folder.is_dir()):
        subprocess.run(["git","fetch","origin"],cwd=absolute_path)
        subprocess.run(["git","checkout",container_ref],cwd=absolute_path)
        subprocess.run(["git","submodule","update","--init","--recursive"],cwd=absolute_path)
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
        
    else :
        
        absolute_path.mkdir(parents=True,exist_ok=True)
        print(f"Cache Directory Successfully made at : {absolute_path} ;)")
        subprocess.run(["git","clone",container_repo_url,absolute_path])
        subprocess.run(["git","checkout",container_ref],cwd=absolute_path)
        subprocess.run(["git","submodule","update","--init","--recursive"],cwd=absolute_path)
        result=subprocess.run(
            ["git","rev-parse","HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=absolute_path
            )
        commit_hash=result.stdout.strip()

        print(f"Resolveed commit: {commit_hash}")
        return commit_hash


    return "Failed to setup container repo Directory :("
        

    


       
    

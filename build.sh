#!/usr/bin/env bash
# exit on error
set -o errexit

echo "--- Adding swap space to handle large build ---"
fallocate -l 1G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile

echo "--- Upgrading build tools ---"
pip install --upgrade pip setuptools wheel

echo "--- Installing Python dependencies from requirements.txt ---"
pip install -r requirements.txt

echo "--- Build complete, removing swap space ---"
swapoff /swapfile
rm /swapfile
```

### Final Steps to Get Your App Live

Now, you just need to upload this updated script to GitHub and tell Render to try one more time.

1.  **Save the changes** to the `build.sh` file in VS Code.

2.  **Upload the fix to GitHub.** Open your terminal in VS Code and run these commands:
    ```bash
    git add .
    git commit -m "Add memory swap to build script for dlib compilation"
    git push
    


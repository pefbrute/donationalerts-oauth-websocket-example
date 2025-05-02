#!/bin/bash

# Load environment variables from .env file
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
else
    echo "Error: .env file not found."
    exit 1
fi

# Function to check command execution
check_command() {
    if [ $? -ne 0 ]; then
        echo "Error: $1"
        exit 1
    fi
}

# Check if git is installed
if ! command -v git &> /dev/null; then
    echo "Error: git is not installed."
    exit 1
fi

# Ask for the commit hash to return to
read -p "Enter the commit hash you want to return to: " TARGET_COMMIT

# Validate the commit hash
if ! git rev-parse --quiet --verify "$TARGET_COMMIT^{commit}" > /dev/null; then
    echo "Error: Invalid commit hash."
    exit 1
fi

# Return to the specified commit
echo "Returning to commit $TARGET_COMMIT..."
git reset --hard "$TARGET_COMMIT"
check_command "Failed to return to the specified commit" "Successfully returned to commit $TARGET_COMMIT"

# Get the current commit hash (which should now be the target commit)
CURRENT_COMMIT=$(git rev-parse HEAD)
echo "Current commit hash: $CURRENT_COMMIT"

# Push the current state to the remote repository
echo "Pushing current state to remote repository..."
git push -f origin HEAD:main
check_command "Failed to push to remote repository" "Successfully pushed to remote repository."

# Check if sshpass is installed
if ! command -v sshpass &> /dev/null; then
    echo "Error: sshpass is not installed."
    exit 1
fi

# Execute commands on the VPS
echo "Applying changes on the VPS..."
sshpass -p "$VPS_PASSWORD" ssh -o StrictHostKeyChecking=no root@"$VPS_IP" "
    cd $DIRECTORY || exit
    echo 'Fetching latest changes...'
    git fetch origin
    echo 'Resetting to the pushed commit...'
    git reset --hard $CURRENT_COMMIT
    echo 'Cleaning untracked files...'
    git clean -fd
    
    # Stop and remove existing containers
    docker-compose down
    
    # Rebuild and restart containers
    docker-compose build --no-cache
    docker-compose up -d
    
    # Restart Nginx
    systemctl restart nginx
"
check_command "Failed to apply changes on VPS" "Changes successfully applied on VPS."

# Final success message
echo "Revert process completed successfully!"
echo "VPS is now at commit: $CURRENT_COMMIT"
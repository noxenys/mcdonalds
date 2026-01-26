#!/bin/bash

# Configuration
IMAGE_NAME="ghcr.io/noxenys/mcdonalds"
TIMESTAMP=$(date +"%Y%m%d_%H%M")

# Check if version argument is provided
if [ -n "$1" ]; then
  VERSION="$1"
else
  # No argument provided, prompt user
  echo -e "${CYAN}Input Version (e.g., 2.0.1)${NC}"
  echo -e "Press ${YELLOW}ENTER${NC} to use timestamp version: ${YELLOW}v${TIMESTAMP}${NC}"
  read -p "Version: " INPUT_VERSION
  
  if [ -n "$INPUT_VERSION" ]; then
    VERSION="$INPUT_VERSION"
  else
    VERSION="v${TIMESTAMP}"
  fi
fi

TAG_LATEST="${IMAGE_NAME}:latest"
TAG_VERSION="${IMAGE_NAME}:${VERSION}"
COMPOSE_FILE="docker-compose.yml"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}🚀 Starting Automated Publish Process...${NC}"
echo -e "   Target Version: ${YELLOW}${VERSION}${NC}"
if [[ -n "$SEMVER_INPUT" && "$VERSION" == "$SEMVER_INPUT" ]]; then
  echo -e "   (semantic version mode)"
else
  echo -e "   (timestamp version mode)"
fi

# 1. Environment Checks
echo -e "\n${CYAN}[1/5] Checking Environment...${NC}"

if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}❌ Error: Docker is not running.${NC}"
    exit 1
fi

if [ ! -f "$COMPOSE_FILE" ]; then
    echo -e "${RED}❌ Error: $COMPOSE_FILE not found.${NC}"
    exit 1
fi

# Check for git
if ! command -v git &> /dev/null; then
    echo -e "${RED}❌ Error: git is not installed.${NC}"
    exit 1
fi

# 2. Build Image
echo -e "\n${CYAN}[2/5] Building Docker Image...${NC}"
if docker build -t "$TAG_LATEST" -t "$TAG_VERSION" .; then
    echo -e "${GREEN}✅ Build successful.${NC}"
else
    echo -e "${RED}❌ Build failed. Aborting.${NC}"
    exit 1
fi

# 3. Push Images
echo -e "\n${CYAN}[3/5] Pushing to GHCR...${NC}"
echo -e "   Pushing ${TAG_VERSION}..."
if docker push "$TAG_VERSION"; then
    echo -e "${GREEN}✅ Version tag pushed.${NC}"
else
    echo -e "${RED}❌ Failed to push version tag. Please check login (docker login ghcr.io).${NC}"
    exit 1
fi

echo -e "   Pushing ${TAG_LATEST}..."
if docker push "$TAG_LATEST"; then
    echo -e "${GREEN}✅ Latest tag pushed.${NC}"
else
    echo -e "${YELLOW}⚠️  Failed to push latest tag. Continuing anyway...${NC}"
fi

# 4. Update docker-compose.yml
echo -e "\n${CYAN}[4/5] Updating configuration...${NC}"

# Detect OS for sed (macOS requires empty string for -i)
if [[ "$OSTYPE" == "darwin"* ]]; then
    SED_CMD="sed -i ''"
else
    SED_CMD="sed -i"
fi

# 1. Comment out 'build: .' if active
# Finds lines starting with whitespace + "build:", replaces with "# build:"
$SED_CMD -E 's/^([[:space:]]*)build:/\1# build:/' "$COMPOSE_FILE"

# 2. Update 'image:' line
# Finds lines with "image: ghcr.io...", handles optional comment # at start
# Replaces with uncommented image line and new version
# Note: Using | as delimiter to avoid escaping slashes in URL
$SED_CMD -E "s|^([[:space:]]*)(# )?image: ${IMAGE_NAME}.*|\1image: ${TAG_VERSION}|" "$COMPOSE_FILE"

echo -e "${GREEN}✅ Updated $COMPOSE_FILE to use ${TAG_VERSION}${NC}"

echo -e "\n${CYAN}[4.5/5] Pulling latest image and restarting services...${NC}"

# Determine which docker compose command to use
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    DOCKER_COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DOCKER_COMPOSE_CMD="docker-compose"
else
    echo -e "${RED}❌ Error: Neither 'docker compose' nor 'docker-compose' found.${NC}"
    echo -e "${YELLOW}Skipping auto-restart step.${NC}"
    DOCKER_COMPOSE_CMD=""
fi

if [ -n "$DOCKER_COMPOSE_CMD" ]; then
    echo -e "   Using command: ${DOCKER_COMPOSE_CMD}"
    if $DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" pull && $DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" up -d; then
        echo -e "${GREEN}✅ Services restarted with latest image.${NC}"
    else
        echo -e "${YELLOW}⚠️  Failed to restart services. You may need to run manual pull/up commands.${NC}"
    fi
fi

# 5. Git Operations
echo -e "\n${CYAN}[5/5] Git Operations...${NC}"

git add "$COMPOSE_FILE"
git commit -m "chore: deploy version ${VERSION}"

echo -e "${GREEN}✅ Committed changes.${NC}"
echo -e "\n${YELLOW}Ready to push!${NC}"
read -p "Do you want to 'git push' now? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    git push
    echo -e "${GREEN}🚀 Pushed to remote. Zeabur should deploy ${VERSION} soon!${NC}"
else
    echo -e "${YELLOW}Skipped push. Run 'git push' manually when ready.${NC}"
fi

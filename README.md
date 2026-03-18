# AWS Agent mcp server and agaent setup guide

## 1) Update OS

```bash
sudo apt update && sudo apt upgrade -y
````

## 2) Install NVIDIA driver

```bash
sudo apt install -y ubuntu-drivers-common
sudo ubuntu-drivers devices
sudo apt install -y nvidia-driver-580-open
sudo reboot
```

After reboot, SSH back in.

## 3) Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
```

## 4) Install NVIDIA Container Toolkit

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt update && sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

## 5) Verify GPU in Docker

```bash
docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi
```

## 6) Clone repo

```bash
git clone <YOUR_GITHUB_REPO_URL> ~/aws-agent
cd ~/aws-agent
```

## 7) Create Ollama volume

```bash
docker volume create ollama-models
```

## 8) Start backend services

```bash
docker compose up -d --build
```

## 9) Pull Ollama model

```bash
docker exec -it ollama ollama pull qwen3.5:27b
```

## 10) Build agent image

```bash
docker build -t aws-agent ./agent
```

## 11) Run agent

```bash
docker run -it --rm --network host aws-agent python agent.py
```

## 12) Optional aliases

```bash
cat >> ~/.bashrc <<'EOF'

alias run_agent='docker run -it --rm --network host aws-agent python agent.py'
alias run_agent_verbose='docker run -it --rm --network host -e AWS_AGENT_DEBUG=1 aws-agent python agent.py'

EOF

source ~/.bashrc
```

```
```

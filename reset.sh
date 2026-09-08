docker rm -f agent-sbx 2>/dev/null
docker run -d --name agent-sbx --network none \
  --memory 2g --cpus 2 --pids-limit 256 \
  -v "$PWD/workspace:/workspace" -w /workspace \
  python:3.12-slim sleep infinity
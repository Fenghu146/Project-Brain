.PHONY: init test demo server clean

init:
	python3 scripts/init-brain.py

test:
	python3 -m pytest brain-server/tests -v

demo:
	python3 scripts/brain-demo.py

server:
	python3 -m brain_server.server

clean:
	rm -f .brain/brain.db .brain/*.db-journal .brain/*.db-wal .brain/*.db-shm
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null; true

.PHONY: install install-browsers demo test test-integration coverage lint typecheck clean codegen har

install:
	poetry install

install-browsers:
	poetry run playwright install chromium firefox webkit

demo:
	@echo "Starting demo app and running QA agent..."
	FLASK_PORT=5001 poetry run python demo/sample_app/app.py &
	sleep 2
	poetry run qa-agent run --url http://localhost:5001
	@pkill -f "demo/sample_app/app.py" || true

test:
	poetry run pytest tests/unit/ -v

test-integration:
	poetry run pytest tests/integration/ -v -s

coverage:
	poetry run pytest tests/unit/ --cov=src --cov-report=html --cov-report=term-missing

lint:
	poetry run ruff check src/ tests/

typecheck:
	poetry run mypy src/

clean:
	rm -rf reports/*
	touch reports/.gitkeep

codegen:
	poetry run playwright codegen http://localhost:5000

har:
	poetry run playwright open --save-har=reports/dev.har http://localhost:5000

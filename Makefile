run:      ; python main.py
test:     ; python -m pytest tests -q
eval:     ; python -m evaluation.run_eval
migrate:  ; alembic upgrade head
newmig:   ; alembic revision --autogenerate -m "change"

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir torch==2.7.1 --index-url https://download.pytorch.org/whl/cpu
COPY *.py .
ENV OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONUNBUFFERED=1
USER 65534:65534
CMD ["python", "service.py"]

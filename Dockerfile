FROM python:latest
EXPOSE 8000

WORKDIR /judgyse
COPY requirements.txt /judgyse/requirements.txt

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

CMD ["fastapi", "run", "main.py", "--port=8000", "--host=0.0.0.0"]
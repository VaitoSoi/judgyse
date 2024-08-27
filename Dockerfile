FROM python:latest
EXPOSE 8080
# ENV RUN_IN_DOCKER=1
# ENV JUDGYSE_DIR=/judgyse
# ENV TIME_PATH=/usr/bin/time

WORKDIR /judgyse
COPY requirements.txt /judgyse/requirements.txt

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

CMD ["uvicorn", "main:app", "--host=0.0.0.0", "--port=8080"]
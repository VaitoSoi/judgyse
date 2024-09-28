FROM python:latest
EXPOSE 8080
ENV INSIDE_DOCKER=1

VOLUME /judgyse
WORKDIR /judgyse
COPY requirements.txt /judgyse/requirements.txt

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

ENTRYPOINT ["uvicorn", "main:app"]
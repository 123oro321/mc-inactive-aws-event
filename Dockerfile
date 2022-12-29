FROM python:3.9-alpine
COPY requirments.txt /tmp/
RUN pip install -r /tmp/requirments.txt && rm -f /tmp/requirments.txt
COPY main.py /data/
CMD ["python","/data/main.py"]
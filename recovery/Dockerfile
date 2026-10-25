FROM python:3.12-alpine

# Stdlib only: nothing to install, nothing to audit beyond the files below.
WORKDIR /opt
COPY hadc_recovery /opt/hadc_recovery

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python3", "-m", "hadc_recovery"]

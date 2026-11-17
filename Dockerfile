FROM python:3.14-slim

WORKDIR /app

# git is only needed to install twikit from its pinned commit.
COPY requirements.lock ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && pip install --no-cache-dir -r requirements.lock \
    && apt-get purge -y git \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY config.py main.py session_store.py ./

# uid 1600 matches compose `user:` and the host egress rule. PyMySQL looks up
# the uid in passwd at import, so the name has to exist inside the image.
RUN groupadd --gid 1600 app \
    && useradd --uid 1600 --gid 1600 --create-home app
USER app

CMD ["python", "main.py"]

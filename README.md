# LLM Chat Application

A simple, containerized chat application using FastAPI, PostgreSQL, and local LLM integration via LM Studio.

## Application Overview

### Features

- **Containerization**: Docker Compose for service orchestration
- **Async Processing**: Non-blocking LLM calls with loading states
- **State Management**: Database-driven conversation persistence
- **Context Maintenance**: Context maintenance with history truncation
- **Markdown Support**: Rich text formatting for LLM responses

### Technical Stack


| Layer         | Technology                 | Purpose                        |
|---------------|----------------------------|--------------------------------|
| **Frontend**  | HTML + CSS                 | Static, no-JS chat interface   |
| **Backend**   | FastAPI (Python 3.11)      | Async routes, LLM logic        |
| **Database**  | PostgreSQL + SQLAlchemy    | Persist message history        |
| **LLM**       | LM Studio HTTP API         | Local inference                |
| **Infra**     | Docker Compose             | Service orchestration          |

### Architecture

#### Container Design
- **App Container**: Python 3.11 with FastAPI
- **DB Container**: PostgreSQL with persistent volumes

#### Request Flow
1. User submits form → FastAPI receives POST
2. Save user message → Display immediately with spinner
3. Meta refresh → Triggers `/process` endpoint  
4. LLM API call → Parse markdown → Save response
5. Redirect to main page → Show complete conversation

## Quick Start

1. **Install Prerequisites**:
   - Docker Desktop
   - [LM Studio](https://lmstudio.ai) (to serve your local LLM)

2. **Clone & Start Project**:
   ```bash
   git clone <your-repo-url>
   cd aichat
   docker-compose up --build
   ```

3. **Start LM Studio**:
   - Load any supported model (e.g., Mistral, Llama)
   - Launch the local server on port `1234`

4. **Chat Interface**: Visit [http://localhost:8000](http://localhost:8000)

## Project Structure

```
aichat/
├── app/
│   ├── main.py          # FastAPI routes & business logic
│   ├── db.py            # Database configuration
│   ├── models.py        # SQLAlchemy models
│   └── static/
│       └── index.html   # HTML layout
│       └── style.css    # Responsive styling
├── docker-compose.yml   # Service orchestration
├── Dockerfile          # Container definition
├── requirements.txt    # Python dependencies
└── README.md           # Documentation
```

## Configuration

```yaml
# docker-compose.yml
services:
  app:
    build: .
    ports: ["8000:8000"]
    environment:
      - DATABASE_URL=postgresql://chatuser:secretpassword@db:5432/chatdb
  db:
    image: postgres:15
    environment:
      - POSTGRES_DB=chatdb
```

## Development

### Local Setup (Alternative to Docker)
```bash
# Environment
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Database (local PostgreSQL required)
createdb chatdb
uvicorn app.main:app --reload --port 8000
```

### Container Commands
```bash
# View application logs
docker-compose logs -f app

# Database shell
docker-compose exec db psql -U chatuser -d chatdb

# Rebuild from scratch
docker-compose down -v && docker-compose up --build
``` 
# Chat-PDFCompress

This project was built to solve a common problem I often run into. During my time in my master study, I always needed smaller and compressed PDFs for academic papers printing. The chatbot interface makes it easy to generate optimized files that load faster in printers without sacrificing too much quality. Most of the academic research papers use descriptive images in PNG format (lossless compression), which is a bit too heavy on the memory consumption, especially in lightweight use cases like printing.

This project is a lightweight, containerized PDF compression service built with FastAPI, PyMuPDF, asyncio, and Google ADK (Agent Development Kit) tools. Users can submit a PDF URL through a simple web interface, where a backend agent interprets user instructions (image formats, compression level, resize ratio, and grayscale conversion) and sends a job to an asynchronous worker. The worker downloads the file with live progress updates, processes and compresses images inside the PDF, and generates a downloadable optimized version. Progress is streamed back to the frontend in real time using WebSockets, giving users a dynamic and responsive experience.

The system runs fully in Docker, with a shared volume between the worker and client for temporary file storage for simplicity (extendable using blob storage). The frontend is served directly from FastAPI with Bootstrap5, featuring a chat-style interaction model, job cards, downloadable results, and live status updates. This architecture keeps the service modular, scalable, and easy to deploy to either locally or on cloud platforms supporting Docker.

| Description | Data Scheme |
|-----------------|-----------------|
| ![Original](description.jpg) | ![Rendered](data_scheme.jpg) |

You can see the short demo of the app [here](https://youtu.be/krccLrRhSno).

## Features

- Chat-based PDF compression powered by **Google ADK**
- Adjustable compression parameters (format, quality, resize ratio, and grayscale)
- Real-time asynchronous progress updates via **WebSocket**
- Containerized deployment


## Project Structure


## Getting Started

### 1. Clone the repository

```bash
git clone https://github.com/yudzyoga/chat-pdfcompress.git
cd chat-pdfcompress/app
```

### 2. Insert Your Google ADK API Key
Follow the original post on how to [generate API key](https://google.github.io/adk-docs/get-started/python/#set-your-api-key), then edit `docker-compose.yml` in the client configuration:
```bash
client:
  environment:
    GOOGLE_API_KEY: "your_google_adk_key_here"
```

### 3. Start the Full System
Build the entire pipeline as dockerized container by running the following command:
```bash
docker compose up --build
```

### 4. Access the Web Interface
Once running, open:
```bash
http://localhost:8000
```
and you will then see the chatbot panel, including job status update panel. The full demo is available on my portfolio website.


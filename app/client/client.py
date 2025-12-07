from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import json 
import uuid
from fastapi import WebSocket

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.agents.llm_agent import Agent
from google.adk.sessions import InMemorySessionService
from google.genai import types
import os
import requests

app = FastAPI()

# Allow frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatMessage(BaseModel):
    message: str

# Serve static folder (JS, CSS, images)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Global connection to worker
reader = None
writer = None

# Buffer for worker updates
progress_updates = asyncio.Queue()


class Helper:
    def __init__(self):
        print("[INFO] PDFCompressor initialized")
        self.reset()

    def set_writer(self, writer):
        self.writer = writer

    def set_format(self, format: str):
        """
        Set the format string configuration. The choices are either jpeg or png. 
        """
        if(format.lower() in ["jpeg", "png"]):
            self.config["format"] = format.lower()

    def set_quality(self, quality: int):
        """
        Set the quality of the image in integer percent [0-100]
        """
        self.config["quality"] = int(max(0, min(100, quality)))

    def set_ratio(self, ratio: float):
        """
        Set the resize ratio if image sizes needed for the image. The range is between [0.0, 1.0]
        """
        self.config["ratio"] = float(max(0.0, min(1.0, ratio)))

    def set_grayscale(self, is_grayscale: bool):
        """
        Set the configuration boolean true to convert into grayscale, otherwise keep the original color.
        """
        self.config["is_grayscale"] = is_grayscale

    def set_is_configured(self):
        """
        Set configuration boolean after every other setup has been set.
        """
        self.has_configured = True

    def is_configured(self) -> bool:
        """
        Check if the user have had configured the conversion configuration or not. Returns bool
        """
        return self.has_configured

    def validate_pdf_url(self, url: str) -> dict:
        """
        Validates if a URL points to a downloadable PDF.
        Returns dict with success, reason, metadata.
        """

        # 1. Basic validation
        if not url.startswith("http://") and not url.startswith("https://"):
            return {"status": False, "reason": "Invalid protocol"}

        try:
            # 2. HEAD request first
            head = requests.head(url, allow_redirects=True, timeout=10)
            status = head.status_code

            if status < 200 or status >= 300:
                return {"status": False, "reason": f"HTTP error: {status}"}

            # 3. Content-Type check
            ctype = head.headers.get("Content-Type", "")
            if "pdf" not in ctype.lower():
                return {"status": False, "reason": f"Not a PDF content type: {ctype}"}

            # 4. Content-Length check
            clen = head.headers.get("Content-Length")
            if clen is not None:
                size_mb = int(clen) / (1024*1024)
                if size_mb > 500: #max 500 MBs
                    return {"status": False, "reason": f"File too large: {size_mb:.2f} MB"}

            # 5. Attempt partial download
            r = requests.get(url, stream=True, timeout=10)
            chunk = next(r.iter_content(chunk_size=1024), None)
            if chunk is None:
                return {"status": False, "reason": "Empty file / cannot stream"}

            self.config["url"] = url
            return {
                "status": True,
                "reason": "PDF is valid and downloadable",
                "content_type": ctype,
                "size_megabytes": int(clen) / (1024*1024) if clen else None
            }

        except Exception as e:
            return {"status": False, "reason": str(e)}

    def reset(self):
        print("[INFO] reset the entire pipeline !")
        self.config = {
            "url": "",
            "format": "jpeg", # jpeg, png, none # type: ignore
            "quality": 100,
            "ratio": 1.0,
            "is_grayscale": False
        }
        self.has_configured = False


    async def execute(self):
        print("[INFO] EXECUTE THE COMMAND !")

        job_id = str(uuid.uuid4())
        json_data = {
                "type": "job",
                "info": {                
                    "job_id": job_id,
                    "url": self.config['url'],
                    "img_format": self.config["format"],
                    "quality": self.config["quality"],
                    "ratio": self.config["ratio"],
                    "is_gray": self.config["is_grayscale"]}
            }
        print(f"✔ Job submitted: {job_id}")

        writer.write((json.dumps(json_data) + "\n").encode())
        await writer.drain()
        return {"reply": f"Job submitted with id: {job_id}"}

class AgentWrapper:
    def __init__(self, app_name: str, user_id: str, session_id: str):
        self.app_name = app_name
        self.user_id = user_id
        self.session_id = session_id

    async def setup(self, root_agent: Agent, initial_state):
        self.session_service = InMemorySessionService()
        self.agent_instance = root_agent
        
        # create the session only once
        await self.session_service.create_session(
            app_name=self.app_name,
            user_id=self.user_id,
            session_id=self.session_id
            # ,
            # state=initial_state
        )

        self.runner_instance = Runner(
            agent=root_agent,
            app_name=self.app_name,
            session_service=self.session_service    
        )

    async def reply(self, message: str, debug: bool = False):
        if(debug):
            pass

        user_content = types.Content(role='user', parts=[types.Part(text=message)])

        final_response_content = "No final response received."
        async for event in self.runner_instance.run_async(user_id=self.user_id, session_id=self.session_id, new_message=user_content):
            if event.is_final_response() and event.content and event.content.parts:
                final_response_content = event.content.parts[0].text

        return final_response_content

# initial state to transfer
APP_NAME = "pdf_compress"
USER_ID = "user1"
SESSION_ID = "session1"
CONFIG = {
    "url": "",          # empty by default
    "format": "jpeg",   # jpeg, png, none # type: ignore
    "quality": 100,     # maintain quality by default
    "ratio": 1.0,       # maintain ratio by default
    "is_gray": False
}

helper = Helper()

# denotes the root agent, simply tuned outside
root_agent = Agent(
    model='gemini-2.5-flash',
    name='root_agent',
    description="Acquire pipeline configuration and execute it.",
    instruction="You are a helpful assistant that helps on the entire pipeline of compressing a pdf file. " \
        "here are some stages you need to acquire the informations before proceeds to execute the entire pipeline:" \
        "0. Always hide other tools for the user to see, keep everything closed and gradually ask for more information. " \
        "1. First and foremost, always ask for the pdf link, then validate the given link by accessing validate_pdf_url(str). " \
        "If the returned json status is False, then simply inform me the reason. " \
        "But if it is True, inform me the pdf size while also ask the next questions." \
        "2. Ask me what kind of conversion the user wants for the document. " \
        "What format [png, jpeg], quality [0-100], image resize ratio [0.0-1.0], and whether conversion to grayscale is needed. " \
        "The accepted formats are only png and jpeg, and you can store it by accessing set_format(str) function. " \
        "The quality input can be stored by accessing set_quality(int) function. " \
        "The resize ration can be stored by accessing set_ratio(float) function. " \
        "The grayscale option can be chosen by accessing set_grayscale(bool) function. " \
        "After finishing all of them, simply store the configuration by accessing set_is_configured(). Then proceed to execute the pipeline by calling execute()." \
        "3. Reset the configuration boolean by calling reset(). This returns to the initial state.",
    tools=[helper.set_format, helper.set_quality, helper.set_ratio, helper.set_is_configured, helper.set_grayscale, helper.validate_pdf_url, helper.execute, helper.reset],
)

# receives the root agent and manage the entire pipeline
wrapper = AgentWrapper(APP_NAME, USER_ID, SESSION_ID)


# ============================================
# Connect to Worker on Startup
# ============================================
@app.on_event("startup")
async def startup():
    global reader, writer
    print("Connecting to worker...")

    # start listening for updates from worker
    reader, writer = await asyncio.open_connection("worker", 8080)
    asyncio.create_task(listen_worker(reader))
    print("Connected to worker.")

    # setup the wrapper
    await wrapper.setup(root_agent, CONFIG)    

    helper.set_writer(writer)

    print("Agent tuned.")

connections: list[WebSocket] = []

async def broadcast_update(msg: dict):
    """Send msg to all connected websockets"""
    for ws in connections:
        try:
            await ws.send_text(json.dumps({"updates": [msg]}))
        except Exception:
            # remove disconnected websockets
            connections.remove(ws)


# ============================================
# Listen for messages from worker
# ============================================
async def listen_worker(reader):
    while True:
        line = await reader.readline()
        if not line:
            break
        msg = json.loads(line.decode())

        print(msg)
        await progress_updates.put(msg)

        # push immediately via websocket
        await broadcast_update(msg)

# Serve index.html on root
@app.get("/")
def root():
    return FileResponse("static/index.html")

@app.websocket("/ws/updates")
async def websocket_updates(ws: WebSocket):
    await ws.accept()
    connections.append(ws)
    try:
        while True:
            # just keep connection alive; we push from listen_worker
            await asyncio.sleep(10)
    except Exception:
        pass
    finally:
        connections.remove(ws)

@app.post("/api/chat")
async def chat(msg: ChatMessage):
    # VERY SIMPLE LOGIC FOR NOW
    cmd = msg.message.strip().split()
            
    if not cmd:
        return {"reply": "nothing"}

    if cmd[0] == "!syscall":
        if len(cmd) < 6:
            return {"reply": "Usage: !syscall <url> <type> <quality> <ratio> <is_gray>"}
        url = cmd[1]
        img_format = str(cmd[2])
        quality = int(cmd[3])
        ratio = float(cmd[4])
        job_id = str(uuid.uuid4())
        is_gray = int(cmd[5]) == 1
        print("--- cmd[5]: ", cmd[5], type(cmd[5]), int(cmd[5]), is_gray)


        # job_id = await submit_job(url, quality, queue)
        json_data = {
                "type": "job",
                "info": {                
                    "job_id": job_id,
                    "url": url,
                    "img_format": img_format,
                    "quality": quality,
                    "ratio": ratio,
                    "is_gray": is_gray}
            }
        print(f"✔ Job submitted: {job_id}")

        writer.write((json.dumps(json_data) + "\n").encode())
        await writer.drain()
        return {"reply": f"✔ Job submitted: {job_id}"}


    else:
        # * use AI Agent here
        bot_reply = await wrapper.reply(msg.message)
        return {"reply": bot_reply}



class DeleteJobRequest(BaseModel):
    job_id: str

@app.post("/api/delete-job")
async def delete_job(req: DeleteJobRequest):
    job_id = req.job_id
    # TODO: delete the actual file on disk / remove from queue
    print(f"Deleting job: {job_id}")

    json_data = {
        "type": "delete",
        "info": {                
            "job_id": job_id
        }
    }
    writer.write((json.dumps(json_data) + "\n").encode())
    await writer.drain()


    # return confirmation
    return {"status": "ok", "job_id": job_id}


@app.get("/download/{filename}")
async def download_file(filename: str):
    path = f"./shared/{filename}"
    if not os.path.isfile(path):
        return {"error": "File not found"}
    return FileResponse(path, filename=filename)
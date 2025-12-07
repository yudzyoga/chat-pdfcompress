import asyncio, json, random
import fitz
import os
import aiohttp

async def handle(reader, writer, job_queue):
    while True:
        line = await reader.readline()
        if not line:
            break

        msg = json.loads(line.decode())
        print("Worker received:", msg)

        if msg["type"] == "job":
            # Attach writer so we can send results back to the client
            await job_queue.put((msg, writer))
        elif msg["type"] == "delete":
            print("delete processed file")

async def download_pdf_with_progress(url, dest, progress_callback):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0

            # Define coarse checkpoints (5 updates)
            checkpoints = [0, 10, 20, 30, 40, 50]
            next_checkpoint_idx = 1  # start looking for 20%

            with open(dest, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 64):  # 64KB chunks
                    f.write(chunk)
                    downloaded += len(chunk)

                    percent = int((downloaded / total) * 50)  # 0–50% reserved for download
                    
                      # If next checkpoint reached → send update
                    if percent >= checkpoints[next_checkpoint_idx]:
                        await progress_callback(checkpoints[next_checkpoint_idx])
                        next_checkpoint_idx += 1

                        if next_checkpoint_idx == len(checkpoints):
                            break   # reached 100%

async def job_worker(job_queue):
    while True:
        msg, writer = await job_queue.get()

        info_job_id = msg["info"]["job_id"]
        info_img_format = msg["info"]["img_format"]
        info_quality = msg["info"]["quality"]
        info_ratio = msg["info"]["ratio"]
        info_grayscale = msg["info"]["is_gray"]

        print(f"Processing job {info_job_id} {info_grayscale}")
        # =========================================================
        # INITIALIZATION — start up some status update
        # =========================================================
        filename_src = os.path.split(msg["info"]["url"])[-1]
        file_dst = os.path.split(filename_src)[-1].split(".pdf")[0]
        filename_dst = f'{file_dst}_converted.pdf'

        update_data = {
            "id": info_job_id,
            "data": {
                "filename": "paper.pdf",
                "status": "Idle",
                "progress": 0,
                "output_filename": "",
                "compress": 0.0,
                "file_size": 0.0
            }
        }

        update_data["data"].update({
            "filename": filename_src,
            "status": "Working...",
            "progress": 0,
            "output_filename": filename_dst
        })
        writer.write((json.dumps(update_data) + "\n").encode())
        await writer.drain()

        # =========================================================
        # PROGRESS CALLBACK — updates frontend during download
        # =========================================================
        async def progress_callback(progress):
            update_data["data"]["progress"] = progress
            update_data["data"]["status"] = f"Downloading..."

            writer.write((json.dumps(update_data) + "\n").encode())
            await writer.drain()

        # =========================================================
        # 1. DOWNLOAD PDF (0–50%)
        # =========================================================
        file_download_path = f"./shared/{filename_src}"
        if not (os.path.exists(file_download_path)):
            await download_pdf_with_progress(msg["info"]["url"], file_download_path, progress_callback)

        # * download PDF
        update_data["data"].update({
            "status": "Converting...",
            "progress": 50
        })
        await writer.drain()

        # * convert pdf based on setup
        file_to_remove = []

        doc = fitz.open(file_download_path)

        for page_index, page in enumerate(doc):
            for image_index, img in enumerate(page.get_images(full=True), start=1):
                xref = img[0]

                # ! IF DELETE
                if(int(info_quality) == 0):
                    page.delete_image(xref=xref)
                else:
                    # Extract pixmap
                    pix = fitz.Pixmap(doc, xref)
                    if pix.n - pix.alpha > 3:
                        pix = fitz.Pixmap(fitz.csRGB, pix)

                    img_pil = pix.pil_image()

                    # force proper color mode
                    if img_pil.mode != "RGB":
                        img_pil = img_pil.convert("RGB")

                    # * if convert to grayscale
                    if(info_grayscale):
                        img_pil = img_pil.convert("L")

                    w, h = img_pil.size
                    img_pil = img_pil.resize((max(int(w * info_ratio), 1), max(int(h * info_ratio), 1)))

                    temp_path = f"./shared/img_{page_index}_{image_index}_gray.{info_img_format}"
                    img_pil.save(temp_path, quality=info_quality, optimize=True)
                    file_to_remove.append(temp_path)

                    page.replace_image(xref=xref, filename=temp_path)

            
            update_data["data"].update({
                "progress": 50 + (50 * (page_index + 1) / len(doc))
            })
            writer.write((json.dumps(update_data) + "\n").encode())
            await writer.drain()

        filepath_out = str(f"./shared/{filename_dst}")
        doc.save(filepath_out)

        # * remove temporary files
        # print(file_to_remove)
        for path in file_to_remove:
            if os.path.isfile(path):
                os.remove(path)

        # * get file size
        # Compute final and original file sizes
        orig_size = os.path.getsize(file_download_path)          # bytes
        out_size  = os.path.getsize(filepath_out)                # bytes

        orig_mb = orig_size / (1024 * 1024)
        out_mb  = out_size  / (1024 * 1024)

        # Compression ratio (how much smaller the output is)
        # e.g. original 100MB → output 20MB → ratio = 80%
        compress_ratio = (1 - (out_size / orig_size)) * 100

        update_data["data"].update({
            "status": "done",
            "progress": 100,
            "output_filename": filename_dst,
            "file_size": f"{round(out_mb, 2)} MB",
            "compress": round(compress_ratio, 2)
        })
        writer.write((json.dumps(update_data) + "\n").encode())
        await writer.drain()
        # await asyncio.sleep(0.1)

        print(f"Job {info_job_id} done")
        job_queue.task_done()

async def main():
    job_queue = asyncio.Queue()

    server = await asyncio.start_server(
        lambda r, w: handle(r, w, job_queue),
        "0.0.0.0",
        8080
    )

    print("Worker running on 8080...")

    # Run server + job processor concurrently
    await asyncio.gather(
        server.serve_forever(),
        job_worker(job_queue)
    )

asyncio.run(main())

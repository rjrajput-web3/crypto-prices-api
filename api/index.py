from mangum import Mangum

from app.main import app

# Vercel serverless entrypoint: every request is rewritten to /api/index
# (see vercel.json), Mangum adapts the FastAPI ASGI app to the Lambda handler.
handler = Mangum(app)

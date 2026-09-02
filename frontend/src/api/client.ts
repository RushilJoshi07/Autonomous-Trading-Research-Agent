import createClient from 'openapi-fetch'
import type { paths } from './schema.gen'

/**
 * The one place this app names the backend's origin. localhost:8000 is
 * where every prior stage-7 verification (Component 1's live checks,
 * Component 2's real-LLM correction test) has run the FastAPI app, and
 * it's also the origin the backend's own CORS middleware (src/api/app.py)
 * is currently configured to allow requests FROM -- not to be confused
 * with this URL, which is what THIS app sends requests TO. A
 * VITE_API_BASE_URL env var overrides it for anything other than local
 * dev (a deployed build, a different port) without touching this file.
 */
const baseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

/**
 * `paths` is generated (schema.gen.ts) directly from the backend's own
 * OpenAPI schema -- every route, method, path/query param, and response
 * shape here is typed from the real Pydantic models in src/api/schemas.py,
 * not hand-maintained. Calling client.GET('/hypotheses/{hypothesis_id}',
 * ...) gets the response typed as the real HypothesisOut shape with zero
 * per-endpoint code on this side; adding a backend route later needs
 * nothing here beyond re-running `npm run generate-api-types`.
 */
export const client = createClient<paths>({ baseUrl })

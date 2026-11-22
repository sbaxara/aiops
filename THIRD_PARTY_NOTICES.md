# Third-party notices

Axara AIOps Community edition is licensed under the GNU AGPL v3.0 or later (see [LICENSE](LICENSE)).
It uses the third-party software and models listed below, each under its own licence. This file
is informational: the licence texts are in each project's distribution, and the licence that a
project ships with takes precedence over this summary.

## 1. Third-party code included in this repository

| File | Project | Licence |
|---|---|---|
| `monitoring-server/static/swagger-ui-bundle.js`, `swagger-ui.css` | Swagger UI (SmartBear) | Apache-2.0 |

The bundle refers to `swagger-ui-bundle.js.LICENSE.txt` for the licences of the libraries it
contains; that file is not in this repository yet. **Before publication**, add it from the
`swagger-ui-dist` release the bundle came from (or replace both files with a current release and
its licence file).

No other third-party source code is copied into this repository.

## 2. Downloaded at build or run time (not in this repository)

### Container images (`monitoring-server/docker-compose.yml`, Dockerfiles)

| Image | Project | Licence |
|---|---|---|
| `python:3.14-slim` | Python (PSF) on Debian | PSF-2.0; Debian packages under their own licences |
| `postgres:19` | PostgreSQL | PostgreSQL Licence |
| `chromadb/chroma:1.5.9` | Chroma | Apache-2.0 |
| `ollama/ollama:0.40.16` | Ollama | MIT |
| `node:26-alpine` (dashboard build stage only) | Node.js on Alpine | MIT; Alpine packages under their own licences |
| `nginx:stable-alpine` | nginx | BSD-2-Clause |

### Models

| Model | Used for | Licence |
|---|---|---|
| Gemma 4 (`gemma4:26b`, default `LLM_MODEL`), pulled by Ollama | Incident analysis | Apache-2.0 (Google) |
| `sentence-transformers/all-MiniLM-L6-v2`, downloaded during the server image build | Incident memory embeddings | Apache-2.0 |

Another model chosen with `LLM_MODEL` is used under that model's own licence.

### Browser for browser monitors (agent)

Playwright downloads Chromium (headless shell): BSD-3-Clause, with third-party components under
their own licences (see `chrome://credits`).

### Note on the server image and NVIDIA libraries

`sentence-transformers` uses PyTorch (2.15, pinned). On Linux the default PyTorch wheels bring the
NVIDIA CUDA 13 libraries (`nvidia-*`, `cuda-*` packages), which are distributed under NVIDIA's proprietary
licence, not an open-source licence. They are downloaded when you build the image and are not part
of this repository.

## 3. Python packages — monitoring server (132, including dependencies)

| Package | Version | Licence |
|---|---|---|
| aiohappyeyeballs | 2.7.1 | Python Software Foundation License |
| aiohttp | 3.14.5 | Apache-2.0 AND MIT |
| aiohttp-retry | 2.9.1 | MIT |
| aiosignal | 1.4.0 | Apache-2.0 |
| alembic | 1.20.1 | MIT |
| annotated-doc | 0.0.7 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.16.0 | MIT |
| attrs | 26.1.0 | MIT |
| bcrypt | 5.0.0 | Apache-2.0 |
| build | 1.6.1 | MIT |
| certifi | 2026.11.20 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| chromadb | 1.5.9 | Apache-2.0 |
| click | 8.5.1 | BSD-3-Clause |
| cloudpickle | 3.1.3 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| cuda-bindings | 13.5.0 | Apache-2.0 |
| cuda-pathfinder | 1.9.1 | Apache-2.0 |
| cuda-toolkit | 13.2.2 | NVIDIA proprietary licence (NVIDIA Software License Agreement) |
| durationpy | 0.11 | MIT |
| fastapi | 0.165.0 | MIT |
| filelock | 4.3.0 | MIT |
| flatbuffers | 25.12.19 | Apache-2.0 |
| frozenlist | 1.8.0 | Apache-2.0 |
| fsspec | 2026.9.0 | BSD-3-Clause |
| googleapis-common-protos | 1.76.0 | Apache-2.0 |
| grpcio | 1.85.3 | Apache-2.0 |
| h11 | 0.16.0 | MIT |
| hf-xet | 1.8.0 | Apache-2.0 |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpcore2 | 2.16.2 | BSD-3-Clause |
| httptools | 0.9.0 | MIT |
| httpx | 0.28.1 | BSD |
| httpx2 | 2.16.2 | BSD-3-Clause |
| huggingface_hub | 2.4.0 | Apache-2.0 |
| idna | 3.20 | BSD-3-Clause |
| importlib_resources | 7.1.0 | Apache-2.0 |
| Jinja2 | 3.1.6 | BSD |
| joblib | 1.6.0 | BSD-3-Clause |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| kubernetes | 37.0.3 | Apache-2.0 |
| Mako | 1.4.3 | MIT |
| markdown-it-py | 4.3.0 | MIT |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| mdurl | 0.1.2 | MIT |
| mmh3 | 5.3.1 | MIT |
| mpmath | 1.3.0 | BSD |
| multidict | 7.2.1 | Apache-2.0 |
| narwhals | 2.29.0 | MIT |
| networkx | 3.7 | BSD-3-Clause |
| numpy | 2.5.5 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| nvidia-cublas | 13.4.1.3 | LicenseRef-NVIDIA-Proprietary |
| nvidia-cuda-cccl | 13.3.5.0.6 | LicenseRef-NVIDIA-Proprietary |
| nvidia-cuda-cupti | 13.2.86 | LicenseRef-NVIDIA-Proprietary |
| nvidia-cuda-nvrtc | 13.2.86 | LicenseRef-NVIDIA-Proprietary |
| nvidia-cuda-runtime | 13.2.86 | LicenseRef-NVIDIA-Proprietary |
| nvidia-cudnn-cu13 | 9.26.0.51 | LicenseRef-NVIDIA-Proprietary |
| nvidia-cufft | 12.2.0.57 | LicenseRef-NVIDIA-Proprietary |
| nvidia-cufile | 1.17.1.22 | LicenseRef-NVIDIA-Proprietary |
| nvidia-cusolver | 12.2.0.11 | LicenseRef-NVIDIA-Proprietary |
| nvidia-cusparse | 12.7.10.12 | LicenseRef-NVIDIA-Proprietary |
| nvidia-cusparselt-cu13 | 0.8.1 | NVIDIA Proprietary Software |
| nvidia-nccl-cu13 | 2.30.7 | LicenseRef-NVIDIA-Proprietary |
| nvidia-nvjitlink | 13.5.37 | LicenseRef-NVIDIA-Proprietary |
| nvidia-nvshmem-cu13 | 3.7.2 | LicenseRef-NVIDIA-Proprietary |
| nvidia-nvtx | 13.2.86 | LicenseRef-NVIDIA-Proprietary |
| oauthlib | 4.0.1 | BSD-3-Clause |
| onnxruntime | 1.32.0 | MIT |
| opentelemetry-api | 1.45.2 | Apache-2.0 |
| opentelemetry-exporter-otlp-common | 0.66b2 | Apache-2.0 |
| opentelemetry-exporter-otlp-proto-common | 1.45.2 | Apache-2.0 |
| opentelemetry-exporter-otlp-proto-grpc | 1.45.2 | Apache-2.0 |
| opentelemetry-proto | 1.45.2 | Apache-2.0 |
| opentelemetry-sdk | 1.45.2 | Apache-2.0 |
| opentelemetry-semantic-conventions | 0.66b2 | Apache-2.0 |
| orjson | 3.13.0 | MPL-2.0 AND (Apache-2.0 OR MIT) |
| overrides | 7.7.0 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| propcache | 0.5.4 | Apache-2.0 |
| protobuf | 7.36.2 | 3-Clause BSD |
| psycopg | 3.3.9 | LGPL-3.0-only |
| psycopg-binary | 3.3.9 | LGPL-3.0-only |
| pybase64 | 1.5.2 | BSD-2-Clause |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.14.4 | MIT |
| pydantic-settings | 2.16.3 | MIT |
| pydantic_core | 2.50.5 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| PyJWT | 2.15.1 | MIT |
| PyPika | 0.51.1 | Apache-2.0 |
| pyproject_hooks | 1.3.3 | MIT |
| python-dateutil | 4.0.0 | BSD-3-Clause |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| python-multipart | 0.0.34 | Apache-2.0 |
| PyYAML | 6.0.4 | MIT |
| referencing | 0.37.0 | MIT |
| regex | 2026.11.19 | Apache-2.0 AND CNRI-Python |
| requests | 2.34.2 | Apache-2.0 |
| requests-oauthlib | 2.0.0 | BSD |
| rich | 15.0.0 | MIT |
| rpds-py | 2026.9.1 | MIT |
| safetensors | 0.8.0 | Apache-2.0 |
| scikit-learn | 1.9.1 | BSD-3-Clause |
| scipy | 1.18.1 | BSD |
| sentence-transformers | 6.3.0 | Apache-2.0 |
| setuptools | 84.2.1 | MIT |
| shellingham | 1.5.4 | ISC License (ISCL) |
| SQLAlchemy | 2.1.9 | MIT |
| starlette | 1.10.0 | BSD-3-Clause |
| sympy | 1.14.0 | BSD |
| tenacity | 9.2.1 | Apache-2.0 |
| threadpoolctl | 3.8.0 | BSD-3-Clause |
| tokenizers | 0.23.3 | Apache-2.0 |
| torch | 2.15.0 | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND ISC AND MIT AND Zlib |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| transformers | 5.24.0 | Apache-2.0 License |
| triton | 3.9.0 | MIT |
| truststore | 0.10.5 | MIT |
| typer | 0.29.2 | MIT |
| typing-inspection | 0.4.5 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.6 | Apache-2.0 |
| urllib3 | 2.8.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| uvloop | 0.23.0 | Apache-2.0; MIT |
| watchfiles | 1.3.0 | MIT |
| websocket-client | 1.9.2 | Apache-2.0 |
| websockets | 17.4 | BSD-3-Clause |
| yarl | 1.25.1 | Apache-2.0 |

## 4. Python packages — agent (17, including dependencies)

| Package | Version | Licence |
|---|---|---|
| certifi | 2026.11.20 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| dnspython | 2.9.2 | ISC |
| greenlet | 3.5.6 | MIT AND PSF-2.0 |
| idna | 3.20 | BSD-3-Clause |
| playwright | 1.65.2 | Apache-2.0 |
| psutil | 7.2.2 | BSD-3-Clause |
| psycopg2-binary | 2.9.13 | GNU Library or Lesser General Public License (LGPL) |
| pycparser | 3.12 | BSD-3-Clause |
| pymongo | 4.20.1 | Apache-2.0 |
| PyMySQL | 1.2.4 | MIT |
| pywin32 | 312 | PSF-2.0 (Windows only) |
| PyYAML | 6.0.4 | MIT |
| requests | 2.34.2 | Apache-2.0 |
| urllib3 | 2.8.0 | MIT |

## 5. JavaScript packages — dashboard (117 runtime packages, including dependencies)

Build-only tools (Vite, Tailwind CSS, ESLint and their dependencies) are not shipped in the built
dashboard and are not listed.

| Package | Version | Licence |
|---|---|---|
| @reactflow/background | 11.3.14 | MIT |
| @reactflow/controls | 11.2.14 | MIT |
| @reactflow/core | 11.11.4 | MIT |
| @reactflow/minimap | 11.7.14 | MIT |
| @reactflow/node-resizer | 2.2.14 | MIT |
| @reactflow/node-toolbar | 1.3.14 | MIT |
| @reduxjs/toolkit | 2.15.0 | MIT |
| @standard-schema/spec | 1.1.0 | MIT |
| @standard-schema/utils | 0.3.0 | MIT |
| @tanstack/query-core | 5.108.2 | MIT |
| @tanstack/react-query | 5.108.2 | MIT |
| @types/d3 | 7.4.3 | MIT |
| @types/d3-array | 3.2.2 | MIT |
| @types/d3-axis | 3.0.6 | MIT |
| @types/d3-brush | 3.0.6 | MIT |
| @types/d3-chord | 3.0.6 | MIT |
| @types/d3-color | 3.1.3 | MIT |
| @types/d3-contour | 3.0.6 | MIT |
| @types/d3-delaunay | 6.0.4 | MIT |
| @types/d3-dispatch | 3.0.7 | MIT |
| @types/d3-drag | 3.0.7 | MIT |
| @types/d3-dsv | 3.0.7 | MIT |
| @types/d3-ease | 3.0.2 | MIT |
| @types/d3-fetch | 3.0.7 | MIT |
| @types/d3-force | 3.0.10 | MIT |
| @types/d3-format | 3.0.4 | MIT |
| @types/d3-geo | 3.1.1 | MIT |
| @types/d3-hierarchy | 3.1.7 | MIT |
| @types/d3-interpolate | 3.0.4 | MIT |
| @types/d3-path | 3.1.1 | MIT |
| @types/d3-polygon | 3.0.2 | MIT |
| @types/d3-quadtree | 3.0.6 | MIT |
| @types/d3-random | 3.0.4 | MIT |
| @types/d3-scale | 4.0.9 | MIT |
| @types/d3-scale-chromatic | 3.1.0 | MIT |
| @types/d3-selection | 3.0.12 | MIT |
| @types/d3-shape | 3.2.0 | MIT |
| @types/d3-time | 3.0.4 | MIT |
| @types/d3-time-format | 4.0.3 | MIT |
| @types/d3-timer | 3.0.2 | MIT |
| @types/d3-transition | 3.0.9 | MIT |
| @types/d3-zoom | 3.0.9 | MIT |
| @types/geojson | 7946.0.16 | MIT |
| @types/use-sync-external-store | 0.0.6 | MIT |
| agent-base | 6.0.2 | MIT |
| asynckit | 0.4.0 | MIT |
| axios | 1.21.1 | MIT |
| call-bind-apply-helpers | 1.0.2 | MIT |
| classcat | 5.0.5 | MIT |
| clsx | 2.1.1 | MIT |
| combined-stream | 1.0.8 | MIT |
| cookie | 1.1.1 | MIT |
| d3-array | 3.2.5 | ISC |
| d3-color | 3.1.1 | ISC |
| d3-dispatch | 3.0.2 | ISC |
| d3-drag | 3.0.1 | ISC |
| d3-ease | 3.0.2 | BSD-3-Clause |
| d3-format | 3.1.3 | ISC |
| d3-interpolate | 3.0.2 | ISC |
| d3-path | 3.1.1 | ISC |
| d3-scale | 4.0.3 | ISC |
| d3-selection | 3.0.1 | ISC |
| d3-shape | 3.2.1 | ISC |
| d3-time | 3.1.1 | ISC |
| d3-time-format | 4.1.1 | ISC |
| d3-timer | 3.0.2 | ISC |
| d3-transition | 3.0.2 | ISC |
| d3-zoom | 3.0.1 | ISC |
| dagre | 0.8.5 | MIT |
| date-fns | 4.4.0 | MIT |
| debug | 4.4.3 | MIT |
| decimal.js-light | 2.5.1 | MIT |
| delayed-stream | 1.0.0 | MIT |
| dunder-proto | 1.0.1 | MIT |
| es-define-property | 1.0.1 | MIT |
| es-errors | 1.3.0 | MIT |
| es-object-atoms | 1.1.2 | MIT |
| es-set-tostringtag | 2.1.0 | MIT |
| es-toolkit | 1.53.2 | MIT |
| eventemitter3 | 5.0.4 | MIT |
| follow-redirects | 1.16.3 | MIT |
| form-data | 4.0.6 | MIT |
| function-bind | 1.1.2 | MIT |
| get-intrinsic | 1.3.0 | MIT |
| get-proto | 1.0.1 | MIT |
| gopd | 1.2.0 | MIT |
| graphlib | 2.1.8 | MIT |
| has-symbols | 1.1.0 | MIT |
| has-tostringtag | 1.0.2 | MIT |
| hasown | 2.0.4 | MIT |
| https-proxy-agent | 5.0.1 | MIT |
| immer | 11.2.10 | MIT |
| internmap | 2.0.3 | ISC |
| lodash | 4.18.1 | MIT |
| lucide-react | 1.77.0 | ISC |
| math-intrinsics | 1.1.0 | MIT |
| mime-db | 1.52.0 | MIT |
| mime-types | 2.1.35 | MIT |
| ms | 2.1.3 | MIT |
| proxy-from-env | 2.1.0 | MIT |
| react | 19.3.1 | MIT |
| react-dom | 19.3.1 | MIT |
| react-is | 19.3.1 | MIT |
| react-redux | 9.3.1 | MIT |
| react-router | 7.18.6 | MIT |
| react-router-dom | 7.18.6 | MIT |
| reactflow | 11.11.4 | MIT |
| recharts | 3.11.0 | MIT |
| redux | 5.0.1 | MIT |
| redux-thunk | 3.1.0 | MIT |
| reselect | 5.2.0 | MIT |
| scheduler | 0.28.0 | MIT |
| set-cookie-parser | 2.7.3 | MIT |
| tiny-invariant | 1.3.3 | MIT |
| use-sync-external-store | 1.7.0 | MIT |
| victory-vendor | 37.4.1 | MIT AND ISC AND BSD-3-Clause |
| zustand | 4.5.7 | MIT |

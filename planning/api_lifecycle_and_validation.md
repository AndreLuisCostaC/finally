# API Lifecycle and Validation

## 1. What is the API lifecycle?

The API lifecycle is the **end-to-end journey of an API** from the moment it is conceived to the moment it is retired. It is not just about coding — it covers strategy, design, testing, deployment, monitoring, and eventual decommissioning.

---

## 2. The stages of the API lifecycle

### Stage 1 — Planning and strategy
This is the "why" before the "how."

- Define the **business problem** the API solves
- Identify **consumers** (internal teams, partners, public developers)
- Decide on the **API style** (REST, GraphQL, gRPC, etc.)
- Define **ownership** — who maintains it, who approves changes
- Establish **versioning strategy** upfront (e.g., `/v1/`, `/v2/`)

---

### Stage 2 — Design (Contract-first)
The contract is written **before any code is written**. This is called the **design-first** or **contract-first** approach.

- Write the OpenAPI/Swagger spec or equivalent
- Define all endpoints, request/response schemas, status codes, and auth
- Share the draft contract with consumers for early feedback
- Lock the contract before development starts

**Why design first?** It forces teams to think clearly about the interface before getting lost in implementation details.

```yaml
# Example: designing the contract before coding
POST /orders:
  requestBody:
    product_id: integer (required)
    quantity: integer (required, min: 1)
  response 201:
    order_id: string
    status: "pending"
    total: float
  response 422:
    error: "insufficient_stock"
    message: string
```

---

### Stage 3 — Development
The implementation phase, guided by the contract.

- Backend implements endpoints to match the contract exactly
- Pydantic (FastAPI), Zod (Node), or similar tools enforce schema validation at runtime
- Mock servers can be generated from the contract so frontend development can proceed in parallel
- Unit tests are written against the contract

---

### Stage 4 — Testing and validation
This is where the contract is verified against the actual implementation. More detail in Section 3 below.

- Contract testing
- Integration testing
- Security testing
- Performance/load testing

---

### Stage 5 — Publishing and documentation
Making the API discoverable and usable.

- Auto-generate documentation from the OpenAPI spec (Swagger UI, Redoc)
- Publish to a developer portal or internal wiki
- Provide SDKs or code samples for common languages
- Document authentication flows, rate limits, and error codes clearly

---

### Stage 6 — Deployment and versioning
Rolling out the API in a controlled way.

- Deploy behind an **API Gateway** (AWS API Gateway, Kong, NGINX)
- Apply rate limiting, authentication enforcement, and logging at the gateway level
- Use versioning to avoid breaking existing consumers:

```
/api/v1/products   ← stable, existing consumers use this
/api/v2/products   ← new version with breaking changes
```

**Common versioning strategies:**

| Strategy | Example | Notes |
|---|---|---|
| URL versioning | `/v1/users` | Most common, very explicit |
| Header versioning | `API-Version: 2` | Cleaner URLs, harder to test in browser |
| Query param | `/users?version=2` | Simple but less clean |

---

### Stage 7 — Monitoring and observability
Watching the API in production.

- Track **response times**, **error rates**, and **throughput**
- Alert on anomalies (spike in 500 errors, latency above threshold)
- Log all requests with structured logs (request ID, endpoint, status, duration)
- Use tools like Datadog, Prometheus, Grafana, or AWS CloudWatch

Key metrics to monitor:

| Metric | Why it matters |
|---|---|
| P99 latency | Catches slow outliers consumers experience |
| Error rate (4xx/5xx) | Signals broken contracts or backend issues |
| Throughput (req/sec) | Capacity planning |
| Availability (uptime %) | SLA compliance |

---

### Stage 8 — Versioning and evolution
Managing change without breaking consumers.

**Non-breaking changes (safe to do):**
- Adding new optional fields to a response
- Adding new optional request parameters
- Adding new endpoints

**Breaking changes (require a new version):**
- Removing or renaming fields
- Changing a field's data type
- Changing required/optional status of a field
- Changing status codes

**Deprecation process:**
1. Announce the deprecation with a timeline
2. Add a `Deprecation` header to responses from the old version
3. Give consumers sufficient migration time (typically 6–12 months)
4. Monitor usage of the old version until it drops to zero
5. Retire the old version

---

### Stage 9 — Retirement / Sunsetting
The controlled shutdown of an API version.

- Communicate sunset date well in advance
- Return `410 Gone` after the endpoint is removed
- Maintain documentation of retired APIs for historical reference
- Ensure all consumers have migrated before shutdown

---

## 3. API Validation in detail

Validation is the process of **ensuring that data flowing in and out of the API conforms to the contract**. It operates at multiple layers.

---

### Layer 1 — Request validation (input)

Happens when a request arrives at the API, before any business logic runs.

What gets validated:
- **Required fields** — are all mandatory fields present?
- **Data types** — is `age` an integer, not a string?
- **Format** — is the email a valid email format?
- **Range/constraints** — is `quantity` between 1 and 1000?
- **Enum values** — is `status` one of `["pending", "paid", "cancelled"]`?

**FastAPI + Pydantic example:**
```python
from pydantic import BaseModel, Field, EmailStr
from enum import Enum

class OrderStatus(str, Enum):
    pending = "pending"
    paid = "paid"
    cancelled = "cancelled"

class CreateOrderRequest(BaseModel):
    product_id: int
    quantity: int = Field(gt=0, le=1000)  # must be 1–1000
    customer_email: EmailStr
    status: OrderStatus = OrderStatus.pending

# FastAPI automatically returns 422 with details if validation fails
@app.post("/orders")
def create_order(body: CreateOrderRequest):
    ...
```

If the request fails validation, the API returns **HTTP 422 Unprocessable Entity** with a clear error:

```json
{
  "detail": [
    {
      "loc": ["body", "quantity"],
      "msg": "ensure this value is greater than 0",
      "type": "value_error.number.not_gt"
    }
  ]
}
```

---

### Layer 2 — Business logic validation

After the request passes schema validation, business rules are applied.

Examples:
- Does the product actually exist in the database?
- Is there enough stock for the requested quantity?
- Does the user have permission to perform this action?
- Is the coupon code valid and not expired?

```python
def create_order(body: CreateOrderRequest, db: Session):
    product = db.get(Product, body.product_id)

    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    if product.stock < body.quantity:
        raise HTTPException(status_code=422, detail="Insufficient stock")

    # proceed with order creation
```

---

### Layer 3 — Response validation (output)

Ensures the API returns exactly what the contract promises.

- Validate that response fields match the declared schema
- Strip sensitive fields (passwords, internal IDs) before returning
- FastAPI does this automatically when you declare a `response_model`

```python
class OrderResponse(BaseModel):
    order_id: str
    status: OrderStatus
    total: float
    # note: no internal fields like db_row_id or cost_price

@app.post("/orders", response_model=OrderResponse)
def create_order(body: CreateOrderRequest):
    ...
```

---

### Layer 4 — Contract testing

Automated tests that verify the **live API matches the written contract**.

Tools:
- **Pact** — consumer-driven contract testing, both sides verify the contract
- **Schemathesis** — auto-generates test cases from OpenAPI specs and fires them at the live API
- **Dredd** — runs OpenAPI/API Blueprint specs against a running server

```bash
# Schemathesis example — tests every endpoint in the spec
schemathesis run openapi.yaml --url http://localhost:8000
```

---

### Layer 5 — Security validation

- **Authentication** — is the JWT token valid and not expired?
- **Authorization** — does this user's role allow this action?
- **Input sanitization** — prevent SQL injection, XSS via input
- **Rate limiting** — block excessive requests per client

---

## 4. Full picture summary

```
Planning → Design (contract) → Develop → Test/Validate → Publish
    ↓                                                        ↓
Retire ← Monitor ← Deploy ← Document ←———————————————————————
```

| Lifecycle stage | Key output |
|---|---|
| Planning | Scope, consumer list, versioning strategy |
| Design | OpenAPI spec / contract |
| Development | Working implementation matching the contract |
| Validation | Passing contract, integration, and security tests |
| Publishing | Live docs, developer portal |
| Monitoring | Dashboards, alerts, SLA reports |
| Evolution | New versions, deprecation notices |
| Retirement | Sunset, 410 responses, migration complete |

The key principle running through all stages is: **the contract is the single source of truth**, and every stage either produces, enforces, or protects it.

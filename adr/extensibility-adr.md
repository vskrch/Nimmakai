# Potato — Routing Extensibility & Admin Revamp ADR

> **Status**: Draft — Proposed Architecture
> **Date**: 2026-07-25  
> **Scope**: New Providers, Prompt-Understanding Router, Custom Catalog Override, Admin Revamp  
> **Constraint**: Existing core routing system must remain unchanged.

---

## 1. Problem Statement

Potato requires several new additive capabilities to enhance flexibility and cost-efficiency:
1. **New Providers**: Native integration with Ollama Cloud and OpenCode Go.
2. **Prompt-Understanding Router**: An intelligent pre-routing step where a lightweight LLM evaluates the raw prompt to explicitly pick the best model for the job (reserving expensive models for complex tasks).
3. **Custom Model Catalog**: Allow admins to strictly define which models map to which tasks, overriding the default auto-router.
4. **Admin UI Revamp**: Add granular toggles for these features and revamp the UI for better usability.

**Crucial Constraint**: The existing core routing system (ladder, scoring, auto-fallback) cannot be modified. These additions must be strictly additive.

---

## 2. Architecture Decision: Pre-Router Interceptor Pattern

To satisfy the constraint that the core router remains untouched, we will implement a **Pre-Router Interceptor Pattern**. 

Currently, when a request hits Potato with `model="auto"`, the core router resolves it using internal classification and ladder mechanics. 

**New Flow**:
Before the core router processes the request, it passes through a chain of interceptors. If an interceptor decides exactly which model to use, it mutates the request payload from `model="auto"` to a specific model ID (e.g., `model="opencode/mimo-v2.5-free"`). 

When the core router receives the request, it sees a specific model instead of `"auto"`. It bypasses its auto-routing logic and treats it as a standard passthrough request with fallback protections. **The core system remains completely unchanged.**

### Interceptor 1: Custom Catalog Override
If the Custom Catalog toggle is enabled, this interceptor checks if the admin has mapped the detected task type (from `IntentClassifier`) to a specific model. If a mapping exists, it mutates the request `model` to that specific model ID.

### Interceptor 2: Prompt-Understanding Router
If the Prompt-Understanding toggle is enabled and the model is still `"auto"`, this interceptor:
1. Gathers the live model pool.
2. Makes a fast, low-latency LLM call (e.g., using an ultra-fast local or cloud model) with the user's prompt and the available pool.
3. The LLM evaluates the complexity (simple text vs complex reasoning) and returns a JSON payload selecting the exact model ID to use.
4. The interceptor mutates the request `model` to this selection.
5. **Fallback**: If the LLM call times out (e.g., > 800ms) or fails, the interceptor gracefully catches the error and leaves the model as `"auto"`, allowing the unchanged core auto-router to handle it.

---

## 3. New Provider Integrations

These will be added to the standard `PROVIDER_PRESETS` list.

### 3.1 Ollama Cloud
- **Base URL**: Derived from `docs.ollama.com/cloud`
- **Auth**: API keys via UI or `OLLAMA_CLOUD_API_KEYS`
- **Toggled**: Independently enablable in the Admin UI.

### 3.2 OpenCode Go
- **Base URL**: `https://opencode.ai/go/v1`
- **Auth**: API keys via UI or `OPENCODE_GO_API_KEYS`
- **Documentation**: Includes troubleshooting links (`opencode.ai/docs/troubleshooting/#model-not-available`) for missing models.
- **Toggled**: Independently enablable in the Admin UI.

---

## 4. Admin UI Revamp

The Admin UI will be rebuilt to support the new feature density.

1. **Routing Capabilities Tab**:
   - Toggle: Prompt-Understanding Router (On/Off)
   - Config: Select the lightweight LLM to power the understanding step.
   - Toggle: Custom Model Catalog (On/Off)
   - Config: UI to map task types to exact model names.
2. **Providers Tab**:
   - Toggles for Ollama Cloud and OpenCode Go.
3. **General UX Revamp**: 
   - Migration from vertical lists to a clean, tabbed interface to group capabilities logically.

---

## 5. Epic and Ticket Breakdown

### Epic 1: Provider Integrations (NMK-EXT-100)
- **NMK-EXT-101**: Add Ollama Cloud to `src/potato/catalog/presets.py`.
- **NMK-EXT-102**: Add OpenCode Go to `src/potato/catalog/presets.py` with standard rate limit profiles and tag definitions.

### Epic 2: Pre-Router Interceptor Framework (NMK-EXT-200)
- **NMK-EXT-201**: Define `PreRouterInterceptor` base class and protocol.
- **NMK-EXT-202**: Wire the interceptor chain into the main API ingress point (e.g., `src/potato/routes/openai.py`) immediately before `ModelSelector.resolve()` is invoked.

### Epic 3: Custom Catalog Override (NMK-EXT-300)
- **NMK-EXT-301**: Extend SQLite database schema to store `custom_catalog_mappings` (intent -> model_id).
- **NMK-EXT-302**: Implement `CustomCatalogInterceptor` that reads these mappings and overrides the request model.

### Epic 4: Prompt-Understanding Router (NMK-EXT-400)
- **NMK-EXT-401**: Implement `PromptUnderstandingInterceptor`.
- **NMK-EXT-402**: Design the prompt template for the understanding LLM. Must include rules for evaluating prompt complexity and JSON output schema.
- **NMK-EXT-403**: Implement strict timeout (e.g., 800ms) and error swallowing to ensure the gateway never fails if the understanding step degrades.

### Epic 5: Admin UI Revamp (NMK-EXT-500)
- **NMK-EXT-501**: Extend SQLite database schema for `extensibility_features` (booleans for the 4 new toggles).
- **NMK-EXT-502**: Add REST endpoints `/admin/extensibility` (GET/PUT) in `src/potato/routes/admin.py`.
- **NMK-EXT-503**: Revamp the frontend layout, implementing the new tabbed design and control panels for the Prompt-Understanding router and Custom Catalog mapping.

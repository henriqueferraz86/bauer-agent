# RFC-005: Bauer OS

Status: Accepted for closed beta

## Summary

Bauer OS is the primary user experience layer for operating Bauer agents, skills, automations, permissions, memory, workspace files and continuous execution.

Bauer OS is not a kernel, bootloader, hardware abstraction layer or traditional operating system. It runs on top of Windows, Linux and macOS as an application shell for agentic work.

## Definition

Bauer OS is the shell/experience that makes the Bauer Runtime understandable and operable by a human user.

The runtime remains below the interface. It owns runs, sessions, events, policies, approvals, schedulers, workers, budgets and runtime adapters. Bauer OS turns those runtime primitives into a coherent product surface.

Agno, when used, is an invisible runtime implementation detail. The final user should not need to know whether a run executed through Bauer Native, Agno or another adapter. The user sees agents, runs, approvals, schedules and outcomes.

## Goals

- Provide one primary place to operate autonomous and supervised agents.
- Make runtime activity visible: what ran, why it ran, what it touched, what it cost and why it was blocked.
- Make permissions and approvals a first-class user workflow.
- Make skills discoverable and governable by capability, risk and permission.
- Make scheduled and continuous execution feel deliberate, observable and stoppable.
- Keep runtime adapters hidden behind a stable Bauer OS experience.

## Non-Goals

- Bauer OS does not replace Windows, Linux or macOS.
- Bauer OS does not manage hardware, processes or filesystems like a kernel.
- Bauer OS does not expose Agno as a product concept to the final user.
- Bauer OS does not require all modules to be implemented as separate native applications.

## Architecture Position

```text
User
  |
  v
Bauer OS
  - Home
  - Agents
  - Skills
  - Runs
  - Approvals
  - Scheduler
  - Memory
  - Files/Workspace
  - OS Control
  - Settings
  - Observability
  |
  v
Bauer Runtime
  - RunManager / SessionManager
  - EventBus / Audit Log / Traces
  - PolicyEngine / ApprovalManager
  - SkillRegistry / SkillExecutor
  - Scheduler / Workers / Recovery
  - Budget / Autonomy
  - Runtime Adapter Interface
  |
  v
Runtime Adapters
  - bauer_native
  - agno
  - future adapters
  |
  v
Host OS
  - Windows
  - Linux
  - macOS
```

## Modules

### Home

The operational landing surface. Shows system health, active runs, pending approvals, budget state, scheduled work and recent outcomes.

### Agents

Create, inspect and run agents. The user chooses intent and capabilities, not the underlying runtime adapter.

### Skills

Browse, validate and inspect skills by capability, permission, risk and platform. Skills are formal units of capability.

### Runs

List and inspect executions. Every run should show input, output, status, session, agent, runtime adapter, cost estimate, tool count, trace and events.

### Approvals

Review actions waiting for user permission. Approval records explain the operation, reason, risk level and associated run.

### Scheduler

Create, pause, resume, delete and manually run persistent tasks. Shows next execution, last run, failures, retry policy and worker status.

### Memory

Inspect and manage persistent context, memories, notes and runtime knowledge that agents can use.

### Files/Workspace

View and manage the workspace under policy control. File access should be explainable and auditable.

### OS Control

Controlled interaction with the host operating system, including shell commands and UI automation. This module is governed by policy and approvals.

### Settings

Configure models, providers, runtime adapters, autonomy mode, budgets, policies and workspace settings.

### Observability

Debug and audit the system. Shows events, traces, audit logs, metrics, budget status, active workers and runtime health.

## Wireframe

```text
+--------------------------------------------------------------------------------+
| Bauer OS                                                        budget $1.42/2 |
+----------------------+---------------------------------------------------------+
| Home                 | Today                                                   |
| Agents               |  - 2 active runs                  1 pending approval     |
| Skills               |  - next scheduled task: daily_project_review 09:00      |
| Runs                 |  - autonomy: supervised          workers: 1 online      |
| Approvals            |                                                         |
| Scheduler            | Active Runs                                             |
| Memory               | +----------------------+----------+---------+----------+ |
| Files/Workspace      | | run id               | agent    | status  | cost     | |
| OS Control           | | run-abc              | coding   | running | $0.03    | |
| Settings             | | run-def              | research | blocked | $0.00    | |
| Observability        | +----------------------+----------+---------+----------+ |
|                      |                                                         |
|                      | Pending Approvals                                       |
|                      | +----------------------+----------+-------------------+ |
|                      | | operation            | risk     | reason            | |
|                      | | shell.execute        | high     | policy requires   | |
|                      | +----------------------+----------+-------------------+ |
+----------------------+---------------------------------------------------------+
```

## User Experience Principles

- The user operates goals, agents and capabilities, not infrastructure.
- Every autonomous action must be inspectable after the fact.
- Blocking should explain why it happened and how to resolve it.
- Risk should be visible before approval, not discovered after execution.
- Budget and autonomy mode should be visible in the primary surface.
- Runtime adapters must not leak into normal workflows unless the user is debugging infrastructure.

## Acceptance Notes

- Bauer OS is explicitly not a kernel or traditional operating system.
- Bauer OS is defined as an application shell and user experience layer.
- Bauer Runtime sits below Bauer OS and owns execution primitives.
- Agno is treated as an invisible adapter behind the runtime interface.

## Closed Beta Decision

For the closed beta, Bauer OS is shipped as the dashboard and local command surface around Bauer Runtime. The runtime remains the product core: runs, sessions, policy, approvals, skills, scheduler, event bus, audit log, memory and adapters.

Agno is supported as an adapter, not as a user-facing product dependency. The default user journey shows agents, runs, approvals and outcomes; adapter details are visible only in settings, runtime diagnostics and debugging views.

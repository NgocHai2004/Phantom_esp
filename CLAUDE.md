# Phantom ESP Project — Claude Code Configuration

## Swarm Mode (MANDATORY)

Every non-trivial task MUST use swarm execution:
1. TeamCreate with descriptive team name
2. Minimum 5 parallel agents
3. Display swarm launch header in CLI
4. Use specialized agents from the agent library

## Agent Library (61 agents available at ~/.claude/agents/)

Key agents for this project:
- **architect** — system design, ESP32 architecture decisions
- **code-reviewer** — code quality review
- **security-reviewer** — security audit
- **performance-optimizer** — optimize ESP32/Python performance
- **python-reviewer** — Python code review (gui.py, de_audio.py, etc.)
- **tdd-guide** — test-driven development
- **build-error-resolver** — fix build/compile errors
- **silent-failure-hunter** — find hidden bugs
- **planner** — task planning and decomposition
- **chief-of-staff** — orchestrate complex multi-step work
- **swarm-coordinator** — decompose and dispatch parallel agents

## Skills to Use

- `/superpowers` — TDD, debugging, parallel agent patterns
- `/context7` — documentation lookup for ESP32, Python libs
- `/ui-ux-pro-max` — for GUI work (gui.py)

## Project Context

- ESP32 client/server firmware (C/C++)
- Python GUI and audio processing
- WiFi communication between ESP32 devices

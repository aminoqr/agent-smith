# Work Division

This was designed as a 2-person project. Amin currently builds everything; his teammate will learn his part from the notes in notes/ and catch up later.

## Person A: Infrastructure
- Sandbox: restricted builtins, import allowlist, path restrictions, timeout, memory limit, no network
- Sandbox REPL CLI and final_answer
- MCP server and MCP client (stdio and HTTP), sandbox manual generation
- SWE-bench filesystem and search tools (read_file, edit_file, list_files, search_code, search_function_or_class_definition_in_code, find_references)

## Person B: Agent
- LLM provider abstraction, multi-key rotation, retries, usage tracking
- Code extraction layer (python blocks, XML, Hermes, ReAct)
- System prompt design and the Thought -> Code -> Observation loop
- MBPP and SWE-bench agent CLIs
- SWE-bench execution tools (run_tests, get_patch, run_command) and Docker handling
- Benchmark runs

## Shared
- Pydantic models and JSON configuration
- Tool tests
- BENCHMARK_REPORT.md and README

## Status
| Area | Built by | Teammate caught up? |
|------|----------|---------------------|
| Person A parts | Amin | not yet |
| Person B parts | Amin | not yet |
| Shared | Amin | not yet |

Learning notes for the teammate: notes/M1.md, notes/M2.md, ...

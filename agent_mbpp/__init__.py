"""The MBPP command line entry point.

Thin on purpose: it parses arguments, builds the pieces from config, and hands
them to `agent_smith`. Everything worth testing lives in the library, so the
same code serves the CLI, the REPL and the tests.
"""

# rules.md

# Creating new tasks with modes

When creating new tasks or switching tasks, ONLY choose a mode based on your current group. All modes within a group have the same suffix. E.g. "Orchestrator-lmstudio" can only switch to other modes that end in "-lmstudio" like "Code-lmstudio".
E.g. #2 "Orchestrator" can only switch to other modes that have no suffix, like "Code".

When creating new tasks or switching tasks, ONLY choose a mode based on your current group. All modes within a group have the same suffix. E.g. "Orchestrator-lmstudio" can only switch to other modes that end in "-lmstudio" like "Code-lmstudio".
E.g. #2 "Orchestrator" can only switch to other modes that have no suffix, like "Code".

Group (ONLY USE THIS GROUP):
Architect-lmstudio
Code-lmstudio
Ask-lmstudio
Debug-lmstudio
Orchestrator-lmstudio
Review-lmstudio


DO NOT USE THESE GROUPS:

Group:
Architect
Code
Ask
Debug
Orchestrator
Review


Group:
Architect-Openrouter
Code-Openrouter
Ask-Openrouter
Debug-Openrouter
Orchestrator-Openrouter
Review-Openrouter



# Sub task competion
Use the "attempt_completion" tool to return to a parent task when a sub task is complete.

# Tool call failures

If a tool call fails, review the response as it contains information on how to correct the tool call, then run the new tool call with corrections made.
Example of tools response that tells you how to correct the read tool call:
""text": "The tool execution failed with the following error:\n<error>\nMissing value for required parameter 'file_path'. Please retry with complete response.\n\n# 
Reminder: Instructions for Tool Use\n\nTools are invoked using the platform's native tool calling mechanism. Each tool requires specific parameters as defined in the tool descriptions. 
Refer to the tool definitions provided in your system instructions for the correct parameter structure and usage examples.\n\nAlways ensure you provide all required parameters for the tool you wish to use.\n</error>""

If a tool call continues to fail, review the internal documentation to find instructions on how to use the tool.
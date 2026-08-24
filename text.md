(sdr-agent) logos@Logos:~/Documents/Projects/agents$ RUN_LIVE_TESTS=1 uv run pytest tests/test_golden_conversations.py::test_qualification_flow_sets_status -v
================================================== test session starts ===================================================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0 -- /home/logos/Documents/Projects/agents/.venv/bin/python
cachedir: .pytest_cache
rootdir: /home/logos/Documents/Projects/agents
configfile: pyproject.toml
plugins: asyncio-1.4.0, anyio-4.14.2
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 1 item                                                                                                         

tests/test_golden_conversations.py::test_qualification_flow_sets_status FAILED                                     [100%]

======================================================== FAILURES ========================================================
__________________________________________ test_qualification_flow_sets_status ___________________________________________

    async def _drive_root_node():
      try:
        if use_scheduler:
          # Rehydration warning: DynamicNodeScheduler relies on session.events scanning.
          # Stateful live EUC/LRO streams may rehydrate freshly if not yet persisted.
          scheduler = DynamicNodeScheduler(state=_LoopState())
          root_ctx._workflow_scheduler = scheduler
    
        try:
>         await root_ctx._run_node_internal(
              root_agent,
              node_input=node_input,
              resume_inputs=resume_inputs,
          )

.venv/lib/python3.12/site-packages/google/adk/runners.py:601: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <google.adk.agents.context.Context object at 0x77b3609eb1a0>
node = LlmAgent(name='QualificationAgent', description='Conduz a qualificação inicial do lead usando perguntas de descoberta ...ff60>], on_model_error_callback=None, before_tool_callback=None, after_tool_callback=None, on_tool_error_callback=None)
node_input = Content(
  parts=[
    Part(
      text='Oi, vi vocês no LinkedIn'
    ),
  ],
  role='user'
)
use_as_output = False, run_id = None, use_sub_branch = False, override_branch = None, override_isolation_scope = None
raise_on_wait = False, return_ctx = False, resume_inputs = None, skip_run_id_validation = False

    async def _run_node_internal(
        self,
        node: NodeLike,
        node_input: Any = None,
        *,
        use_as_output: bool = False,
        run_id: str | None = None,
        use_sub_branch: bool = False,
        override_branch: str | None = None,
        override_isolation_scope: str | None = None,
        raise_on_wait: bool = False,
        return_ctx: bool = False,
        resume_inputs: dict[str, Any] | None = None,
        skip_run_id_validation: bool = False,
    ) -> Any:
      """Executes a node dynamically (Internal Orchestration API).
    
      See public ``run_node`` for public argument details.
      Additional internal args:
        return_ctx: If True, returns the child's Context instead of its output.
      """
    
      if not self._node_rerun_on_resume:
        raise ValueError(
            'A node must have rerun_on_resume=True. Reason is that dynamically'
            ' scheduled nodes might be interrupted, and the workflow'
            ' wakes-up/re-runs the parent node, so it can get the child node'
            ' response.'
        )
    
      from ..workflow.utils._workflow_graph_utils import build_node  # pylint: disable=g-import-not-at-top
    
      built_node = build_node(node)
    
      from ..agents.base_agent import BaseAgent
    
      if isinstance(node, BaseAgent) and isinstance(built_node, BaseAgent):
        built_node.parent_agent = node.parent_agent
    
      # Output delegation: once set, the calling node's own output
      # events are suppressed — the child's output (annotated with
      # output_for) becomes the calling node's output.
      # We validate and set this upfront before entering the loop.
      if use_as_output:
        from ..workflow._workflow import Workflow
    
        if not isinstance(self.node, Workflow):
          if self._output_delegated:
            raise ValueError(
                f'Node {self.node_path} already has a use_as_output delegate.'
            )
          self._output_delegated = True
    
      # Pointers to track the active execution state in the transfer loop.
      # These will be updated dynamically if an agent transfers execution.
      curr_parent_ctx = self
      curr_node = built_node
      curr_run_id = run_id
      curr_input = node_input
    
      # Active Execution Loop: Handles both standard execution and sequential Agent Transfers
      # (e.g. Agent A transferring to Agent B). Instead of recursive execution, we use this
      # loop to execute the target agent in-place, updating pointers and 'continuing' the loop.
      while True:
        curr_use_as_output = use_as_output if (curr_parent_ctx is self) else False
        if self._workflow_scheduler:
          # --- Mode 1: Workflow Execution ---
          # The node is running as part of a Workflow graph. We must delegate execution
          # to the workflow scheduler to handle graph dependencies and state.
          from ..workflow._errors import NodeInterruptedError
    
          # Validate or auto-generate run_id for this scheduler execution.
          if curr_run_id:
            if curr_run_id.isdigit() and not skip_run_id_validation:
              raise ValueError(
                  f'Explicit run_id "{curr_run_id}" for node "{curr_node.name}"'
                  ' must contain non-numeric characters to prevent collision'
                  ' with auto-generated IDs.'
              )
          elif not curr_run_id:
            curr_parent_ctx._child_run_counters[curr_node.name] = (
                curr_parent_ctx._child_run_counters.get(curr_node.name, 0) + 1
            )
            curr_run_id = str(curr_parent_ctx._child_run_counters[curr_node.name])
    
          child_ctx = await curr_parent_ctx._workflow_scheduler(
              curr_parent_ctx,
              curr_node,
              curr_input,
              node_name=curr_node.name,
              use_as_output=curr_use_as_output,
              run_id=curr_run_id,
              use_sub_branch=use_sub_branch,
              override_branch=override_branch,
              override_isolation_scope=override_isolation_scope,
          )
        else:
          # --- Mode 2: Standalone Execution ---
          # The node is running independently (outside of a workflow).
          # We run it directly using NodeRunner.
          child_ctx = await curr_parent_ctx._run_node_standalone(
              curr_node,
              curr_input,
              use_as_output=curr_use_as_output,
              use_sub_branch=use_sub_branch,
              override_branch=override_branch,
              override_isolation_scope=override_isolation_scope,
              run_id=curr_run_id,
              resume_inputs=resume_inputs,
          )
    
        # Extract the transfer target if the node requested an agent transfer.
        transfer_to_agent = (
            child_ctx.actions.transfer_to_agent if child_ctx else None
        )
    
        # Post-Execution Validation: If the caller expects the raw output (not the Context),
        # we check for errors or interrupts and raise them immediately.
        if not return_ctx:
          if child_ctx.error:
            from ..workflow._errors import DynamicNodeFailError
    
>           raise DynamicNodeFailError(
                message=f'Dynamic node {curr_node.name} failed',
                error=child_ctx.error,
                error_node_path=child_ctx.error_node_path,
            )
E           google.adk.workflow._errors.DynamicNodeFailError: Dynamic node QualificationAgent failed

.venv/lib/python3.12/site-packages/google/adk/agents/context.py:603: DynamicNodeFailError

During handling of the above exception, another exception occurred:

    async def test_qualification_flow_sets_status():
        from app.agents.qualification import qualification_agent
        from app.agents.session.state_schema import STATE_QUALIFICATION_STATUS
    
>       state, _response_text = await _run_conversation(
            qualification_agent,
            [
                "Oi, vi vocês no LinkedIn",
                "Temos uns 50 funcionários, orçamento de uns R$5k/mês",
                "Sou eu quem decide isso",
                "Precisamos resolver isso ainda esse trimestre",
            ],
        )

tests/test_golden_conversations.py:98: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
tests/test_golden_conversations.py:61: in _run_conversation
    async for event in runner.run_async(
.venv/lib/python3.12/site-packages/google/adk/runners.py:1111: in run_async
    async for event in agen:
.venv/lib/python3.12/site-packages/google/adk/runners.py:628: in _run_node_async
    await self._cleanup_root_task(task, self.agent.name)
.venv/lib/python3.12/site-packages/google/adk/runners.py:906: in _cleanup_root_task
    await task
.venv/lib/python3.12/site-packages/google/adk/runners.py:610: in _drive_root_node
    raise e.error
.venv/lib/python3.12/site-packages/google/adk/workflow/_node_runner.py:136: in run
    await self._execute_node(ctx, node_input)
.venv/lib/python3.12/site-packages/google/adk/workflow/_node_runner.py:274: in _execute_node
    await self._run_node_loop(ctx, node_input)
.venv/lib/python3.12/site-packages/google/adk/workflow/_node_runner.py:288: in _run_node_loop
    async for event in agen:
.venv/lib/python3.12/site-packages/google/adk/workflow/_base_node.py:166: in run
    async for item in agen:
.venv/lib/python3.12/site-packages/google/adk/agents/llm_agent.py:599: in _run_impl
    async for event in agen:
.venv/lib/python3.12/site-packages/google/adk/workflow/_llm_agent_wrapper.py:370: in run_llm_agent_as_node
    async for event in run_iter:
.venv/lib/python3.12/site-packages/google/adk/agents/base_agent.py:306: in run_async
    async for event in agen:
.venv/lib/python3.12/site-packages/google/adk/agents/llm_agent.py:548: in _run_async_impl
    async for event in agen:
.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py:956: in run_async
    async for event in agen:
.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py:1036: in _run_one_step_async
    async for llm_response in agen:
.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py:1483: in _call_llm_async
    async for event in agen:
.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py:1461: in _call_llm_with_tracing
    async for llm_response in agen:
.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py:1544: in _run_and_handle_error
    async for response in agen:
.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py:419: in _run_and_handle_error
    raise model_error
.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py:396: in _run_and_handle_error
    async for llm_response in agen:
.venv/lib/python3.12/site-packages/google/adk/models/lite_llm.py:3047: in generate_content_async
    yield _model_response_to_generate_content_response(response)
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
.venv/lib/python3.12/site-packages/google/adk/models/lite_llm.py:2109: in _model_response_to_generate_content_response
    llm_response = _message_to_generate_content_response(
.venv/lib/python3.12/site-packages/google/adk/models/lite_llm.py:2183: in _message_to_generate_content_response
    args=_parse_tool_call_arguments(tool_call.function.arguments),
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
.venv/lib/python3.12/site-packages/google/adk/models/lite_llm.py:217: in _parse_tool_call_arguments
    raise json_error
.venv/lib/python3.12/site-packages/google/adk/models/lite_llm.py:198: in _parse_tool_call_arguments
    return json.loads(arguments)
           ^^^^^^^^^^^^^^^^^^^^^
/usr/lib/python3.12/json/__init__.py:346: in loads
    return _default_decoder.decode(s)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <json.decoder.JSONDecoder object at 0x77b4a9ddd610>
s = '{"status": "in_progress", "reasoning": "O lead está no início da conversa e ainda não sabemos se ele é um bom fit."}{"status": "in_progress", "reasoning": "O lead está no início da conversa e ainda não sabemos se ele é um bom fit."}'
_w = <built-in method match of re.Pattern object at 0x77b4a9da1cb0>

    def decode(self, s, _w=WHITESPACE.match):
        """Return the Python representation of ``s`` (a ``str`` instance
        containing a JSON document).
    
        """
        obj, end = self.raw_decode(s, idx=_w(s, 0).end())
        end = _w(s, end).end()
        if end != len(s):
>           raise JSONDecodeError("Extra data", s, end)
E           json.decoder.JSONDecodeError: Extra data: line 1 column 117 (char 116)

/usr/lib/python3.12/json/decoder.py:340: JSONDecodeError
--------------------------------------------------- Captured log call ----------------------------------------------------
WARNING  presidio-analyzer:spacy_nlp_engine.py:61 Failed to enable GPU (cuda), falling back to CPU: Cannot use GPU, CuPy is not installed
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - CreditCardRecognizer supported languages: en, registry supported languages: pt
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - CreditCardRecognizer supported languages: es, registry supported languages: pt
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - CreditCardRecognizer supported languages: it, registry supported languages: pt
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - CreditCardRecognizer supported languages: pl, registry supported languages: pt
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - UsBankRecognizer supported languages: en, registry supported languages: pt
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - UsLicenseRecognizer supported languages: en, registry supported languages: pt
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - UsItinRecognizer supported languages: en, registry supported languages: pt
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - UsPassportRecognizer supported languages: en, registry supported languages: pt
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - UsSsnRecognizer supported languages: en, registry supported languages: pt
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - NhsRecognizer supported languages: en, registry supported languages: pt
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - EsNifRecognizer supported languages: es, registry supported languages: pt
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - EsNieRecognizer supported languages: es, registry supported languages: pt
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - ItDriverLicenseRecognizer supported languages: it, registry supported languages: pt
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - ItFiscalCodeRecognizer supported languages: it, registry supported languages: pt
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - ItVatCodeRecognizer supported languages: it, registry supported languages: pt
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - ItIdentityCardRecognizer supported languages: it, registry supported languages: pt
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - ItPassportRecognizer supported languages: it, registry supported languages: pt
WARNING  presidio-analyzer:recognizers_loader_utils.py:176 Recognizer not added to registry because language is not supported by registry - PlPeselRecognizer supported languages: pl, registry supported languages: pt
ERROR    google_adk.google.adk.workflow._node_runner:_node_runner.py:154 Node execution failed with exception
Traceback (most recent call last):
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/workflow/_node_runner.py", line 136, in run
    await self._execute_node(ctx, node_input)
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/workflow/_node_runner.py", line 274, in _execute_node
    await self._run_node_loop(ctx, node_input)
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/workflow/_node_runner.py", line 288, in _run_node_loop
    async for event in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/workflow/_base_node.py", line166, in run
    async for item in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/agents/llm_agent.py", line 599, in _run_impl
    async for event in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/workflow/_llm_agent_wrapper.py", line 370, in run_llm_agent_as_node
    async for event in run_iter:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/agents/base_agent.py", line 306, in run_async
    async for event in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/agents/llm_agent.py", line 548, in _run_async_impl
    async for event in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py", line 956, in run_async
    async for event in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py", line 1036, in _run_one_step_async
    async for llm_response in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py", line 1483, in _call_llm_async
    async for event in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py", line 1461, in _call_llm_with_tracing
    async for llm_response in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py", line 1544, in _run_and_handle_error
    async for response in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py", line 419, in _run_and_handle_error
    raise model_error
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py", line 396, in _run_and_handle_error
    async for llm_response in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/models/lite_llm.py", line 3047, in generate_content_async
    yield _model_response_to_generate_content_response(response)
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/models/lite_llm.py", line 2109, in _model_response_to_generate_content_response
    llm_response = _message_to_generate_content_response(
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/models/lite_llm.py", line 2183, in _message_to_generate_content_response
    args=_parse_tool_call_arguments(tool_call.function.arguments),
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/models/lite_llm.py", line 217, in _parse_tool_call_arguments
    raise json_error
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/models/lite_llm.py", line 198, in _parse_tool_call_arguments
    return json.loads(arguments)
           ^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.12/json/__init__.py", line 346, in loads
    return _default_decoder.decode(s)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.12/json/decoder.py", line 340, in decode
    raise JSONDecodeError("Extra data", s, end)
json.decoder.JSONDecodeError: Extra data: line 1 column 117 (char 116)
ERROR    google_adk.google.adk.runners:runners.py:910 Root node QualificationAgent failed.
Traceback (most recent call last):
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/runners.py", line 601, in _drive_root_node
    await root_ctx._run_node_internal(
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/agents/context.py", line 603,in _run_node_internal
    raise DynamicNodeFailError(
google.adk.workflow._errors.DynamicNodeFailError: Dynamic node QualificationAgent failed

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/runners.py", line 906, in _cleanup_root_task
    await task
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/runners.py", line 610, in _drive_root_node
    raise e.error
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/workflow/_node_runner.py", line 136, in run
    await self._execute_node(ctx, node_input)
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/workflow/_node_runner.py", line 274, in _execute_node
    await self._run_node_loop(ctx, node_input)
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/workflow/_node_runner.py", line 288, in _run_node_loop
    async for event in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/workflow/_base_node.py", line166, in run
    async for item in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/agents/llm_agent.py", line 599, in _run_impl
    async for event in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/workflow/_llm_agent_wrapper.py", line 370, in run_llm_agent_as_node
    async for event in run_iter:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/agents/base_agent.py", line 306, in run_async
    async for event in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/agents/llm_agent.py", line 548, in _run_async_impl
    async for event in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py", line 956, in run_async
    async for event in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py", line 1036, in _run_one_step_async
    async for llm_response in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py", line 1483, in _call_llm_async
    async for event in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py", line 1461, in _call_llm_with_tracing
    async for llm_response in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py", line 1544, in _run_and_handle_error
    async for response in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py", line 419, in _run_and_handle_error
    raise model_error
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/flows/llm_flows/base_llm_flow.py", line 396, in _run_and_handle_error
    async for llm_response in agen:
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/models/lite_llm.py", line 3047, in generate_content_async
    yield _model_response_to_generate_content_response(response)
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/models/lite_llm.py", line 2109, in _model_response_to_generate_content_response
    llm_response = _message_to_generate_content_response(
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/models/lite_llm.py", line 2183, in _message_to_generate_content_response
    args=_parse_tool_call_arguments(tool_call.function.arguments),
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/models/lite_llm.py", line 217, in _parse_tool_call_arguments
    raise json_error
  File "/home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/models/lite_llm.py", line 198, in _parse_tool_call_arguments
    return json.loads(arguments)
           ^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.12/json/__init__.py", line 346, in loads
    return _default_decoder.decode(s)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.12/json/decoder.py", line 340, in decode
    raise JSONDecodeError("Extra data", s, end)
json.decoder.JSONDecodeError: Extra data: line 1 column 117 (char 116)
==================================================== warnings summary ====================================================
<frozen abc>:106
<frozen abc>:106
<frozen abc>:106
<frozen abc>:106
  <frozen abc>:106: DeprecationWarning: BaseAgentConfig is deprecated and will be removed in future versions. Config is now loaded via reflection so the separate config class is no longer needed.

tests/test_golden_conversations.py::test_qualification_flow_sets_status
  /home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/features/_feature_decorator.py:72: UserWarning: [EXPERIMENTAL] feature FeatureName.PLUGGABLE_AUTH is enabled.
    check_feature_enabled()

tests/test_golden_conversations.py::test_qualification_flow_sets_status
  /home/logos/Documents/Projects/agents/.venv/lib/python3.12/site-packages/google/adk/models/llm_request.py:273: UserWarning: [EXPERIMENTAL] feature FeatureName.JSON_SCHEMA_FOR_FUNC_DECL is enabled.
    declaration = tool._get_declaration()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
================================================ short test summary info =================================================
FAILED tests/test_golden_conversations.py::test_qualification_flow_sets_status - json.decoder.JSONDecodeError: Extra data: line 1 column 117 (char 116)
============================================= 1 failed, 6 warnings in 9.23s ==============================================
(sdr-agent) logos@Logos:~/Documents/Projects/agents$ 
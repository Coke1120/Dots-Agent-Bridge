# Dot-initiated OpenBot task dispatch

The original Phase 2 proposal is implemented through OpenBot's Local scripts message queue. A dot chooses an allowed agent, submits one task and reads the result using its bridge task ID. The bridge correlates the answer with the native OpenBot delivery.

For deployment, grant configuration, retries and an end-to-end acceptance check, see [Dot to OpenBot](agent-dispatch.md).

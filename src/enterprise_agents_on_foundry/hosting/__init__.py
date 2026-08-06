"""The Microsoft Foundry hosting boundary.

A thin adapter that exposes the existing Text-to-SQL graph through the Foundry
Responses protocol. The agent core stays protocol-independent: this package
only maps a Responses turn onto :func:`answer_question` and back, and nothing in
:mod:`enterprise_agents_on_foundry.agents` imports it.
"""

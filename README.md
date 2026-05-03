# Jig

> A device that holds a piece of work in a fixed position and guides the tool operating on it, 
used to ensure accuracy and repeatability across many identical parts.

## Why Jig Exists

Agents can make quick work of focused tasks. Bite-sized chunks of well described and well spec'd tasks
are what an agent is good at. What it's bad at is understanding the "big picture". Why does this app exist?
What tradeoffs can we make? etc. Jig is designed to help agents understand the "big picture" 
by providing a framework for breaking down complex tasks into smaller, more manageable pieces. And
using it's own spec-langauge to describe the tasks and their dependencies, 
making it easier for agents to reason about the overall task and make informed decisions.\

## What Jig seeks to do

Covered more in TENETS.md but here quickly our goals are:

1. Make agents better at what they already do well
2. Make humans better at what <they> do well

## How we hope to achieve that

1. Bite-sized work, coherent whole: focused tickets with proper context driven by larger project and architectural plans
2. Exact context, no more, no less: dedicated context layer that assembles the knowledge agents need to know
3. Clear instructions and boundaries: Well-constructed tickets with specs and validation steps and concurrent-review 
4. Structured, consistent language: Using YAMl-based specs so agents see it as a contract, not a suggestion
5. Both sides earn their best thinking through structure: We walk the human through the whole process of planning their app, and then unleash the agents
"""
quiz_bank.py — Seed quiz questions for the real KU courses.

These are written once and inserted into the DB on first run.
Users can add their own via the API; this just provides a starting point
so the quiz feature works immediately without manual setup.
"""

# Each entry: (topic_name_fragment, question, a, b, c, d, correct, explanation)
# topic_name_fragment is matched case-insensitively against topic names.

QUESTIONS = [
    # ── COSC310 Data Structures ───────────────────────────────────────────────
    ("arrays", "What is the time complexity of accessing an element by index in an array?",
     "O(n)", "O(log n)", "O(1)", "O(n²)", "c",
     "Arrays store elements in contiguous memory. The CPU computes address = base + index × size in constant time."),

    ("linked list", "Which operation is O(1) for a singly linked list but O(n) for an array?",
     "Random access", "Insertion at head", "Binary search", "Sorting", "b",
     "Inserting at the head of a linked list just updates the head pointer. Arrays need shifting."),

    ("stack", "Which data structure follows LIFO (Last In, First Out)?",
     "Queue", "Stack", "Heap", "Graph", "b",
     "A stack pops elements in reverse insertion order — like a stack of plates."),

    ("queue", "Which scheduling algorithm uses a queue (FIFO) structure?",
     "Priority scheduling", "Shortest Job First", "Round Robin", "First-Come-First-Served", "d",
     "FCFS processes tasks in arrival order — exactly what a FIFO queue provides."),

    ("hash", "What is a hash collision?",
     "When two keys map to the same index", "When the hash table is full",
     "When a key is deleted", "When search fails", "a",
     "A collision occurs when hash(k1) == hash(k2) for k1 ≠ k2. Resolved by chaining or open addressing."),

    ("heap", "In a max-heap, where is the largest element?",
     "At any leaf", "At the root", "At the last position", "In the middle", "b",
     "The heap property guarantees parent ≥ children, so the max always sits at the root."),

    ("bst", "What is the worst-case time complexity of search in an unbalanced BST?",
     "O(1)", "O(log n)", "O(n)", "O(n log n)", "c",
     "A degenerate BST (all nodes in a line) degrades to a linked list — O(n) search."),

    ("sort", "Which sorting algorithm has O(n log n) in all cases (best, avg, worst)?",
     "Quick sort", "Merge sort", "Bubble sort", "Insertion sort", "b",
     "Merge sort always divides the array in half and merges — O(n log n) guaranteed."),

    ("graph", "What does BFS (Breadth-First Search) use internally?",
     "Stack", "Priority queue", "Queue", "Array", "c",
     "BFS explores level by level, always processing the nearest nodes first — this requires a FIFO queue."),

    ("complexity", "What does Big-O notation describe?",
     "Exact runtime in seconds", "Average-case memory usage",
     "Upper bound on growth rate", "Number of lines of code", "c",
     "Big-O is an asymptotic upper bound — it tells you how runtime scales with input size, not the exact time."),

    # ── COSC354 Operating Systems ─────────────────────────────────────────────
    ("process", "What is the difference between a process and a thread?",
     "Threads share memory; processes do not",
     "Processes are faster than threads",
     "Threads have separate address spaces",
     "Processes share the CPU register state", "a",
     "Threads within the same process share heap and globals. Separate processes have isolated address spaces."),

    ("scheduling", "Which CPU scheduling algorithm can cause starvation?",
     "Round Robin", "FCFS", "Shortest Job First", "Multilevel Queue", "c",
     "SJF always picks the shortest job. A long job may wait indefinitely if short jobs keep arriving."),

    ("deadlock", "Which of the following is NOT a necessary condition for deadlock?",
     "Mutual exclusion", "Preemption", "Hold and wait", "Circular wait", "b",
     "The four conditions are: mutual exclusion, hold-and-wait, no preemption, circular wait. Preemption *prevents* deadlock."),

    ("memory", "What problem does paging solve?",
     "CPU scheduling inefficiency", "External fragmentation",
     "Thread synchronization", "Deadlock", "b",
     "Paging divides memory into fixed-size frames, eliminating external fragmentation (though introducing internal fragmentation)."),

    ("thread", "What is a race condition?",
     "A process running too fast", "Two threads accessing shared data concurrently with unpredictable results",
     "A deadlock involving two threads", "A memory leak", "b",
     "A race condition occurs when the outcome depends on thread scheduling — non-deterministic bugs are hard to reproduce."),

    # ── COSC330 Artificial Intelligence ──────────────────────────────────────
    ("search", "What distinguishes informed search from uninformed search?",
     "Informed search is always faster",
     "Informed search uses a heuristic function to guide the search",
     "Informed search explores all nodes",
     "Informed search requires more memory", "b",
     "A heuristic h(n) estimates cost from node n to the goal. A* uses f(n)=g(n)+h(n) to find optimal paths efficiently."),

    ("classifier", "In a linear classifier, what does the decision boundary represent?",
     "The training accuracy", "A hyperplane separating classes",
     "The learning rate", "The number of features", "b",
     "A linear classifier finds a hyperplane wx+b=0 that best separates class labels in feature space."),

    ("neural", "What is the role of the activation function in a neural network?",
     "To initialize weights", "To introduce non-linearity",
     "To compute the loss", "To normalize the input", "b",
     "Without non-linear activations, stacking layers collapses to a single linear transformation."),

    ("decision tree", "What does information gain measure in a decision tree?",
     "Depth of the tree", "Reduction in entropy after a split",
     "Number of leaves", "Prediction accuracy", "b",
     "Information gain = entropy(parent) − weighted_avg(entropy(children)). A good split reduces uncertainty."),

    ("game theory", "In minimax, what does the MAX player try to do?",
     "Minimize the opponent's score", "Maximize its own score",
     "Minimize depth", "Maximize branching factor", "b",
     "MAX picks the move with the highest value; MIN picks the lowest. They alternate in the game tree."),

    # ── COSC301 Automata ──────────────────────────────────────────────────────
    ("dfa", "What is the key difference between a DFA and an NFA?",
     "DFAs are faster to simulate",
     "NFAs can have multiple transitions on the same input symbol",
     "DFAs accept more languages",
     "NFAs require more states", "b",
     "An NFA can branch on a symbol or make ε-transitions. Both accept the same class of languages (regular)."),

    ("regular", "Which of the following is NOT a regular language?",
     "All strings over {0,1} with even length",
     "All strings containing '00'",
     "The set {0ⁿ1ⁿ | n ≥ 0}",
     "All strings ending in '1'", "c",
     "0ⁿ1ⁿ requires counting, which finite automata cannot do. It's context-free (accepted by a PDA)."),

    ("turing", "What is the Church-Turing thesis?",
     "All languages are decidable",
     "Any effectively computable function can be computed by a Turing machine",
     "Turing machines are faster than real computers",
     "Context-free languages are decidable", "b",
     "The thesis equates intuitive computability with Turing computability — it's a thesis, not a proven theorem."),
]

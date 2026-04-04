import sys
import types
from unittest.mock import MagicMock
import pytest
import numpy as np

class MockPandasTA(types.ModuleType):
    pass

class MockAdvancedTA(types.ModuleType):
    pass

# We will save the original modules and restore them later
original_modules = {}
for mod in ['pandas_ta', 'advanced_ta']:
    if mod in sys.modules:
        original_modules[mod] = sys.modules[mod]

sys.modules['pandas_ta'] = MockPandasTA('pandas_ta')
sys.modules['advanced_ta'] = MockAdvancedTA('advanced_ta')

# Importing EvolutionaryAgent here relies on the mocks
from myra_app.ml_engine import EvolutionaryAgent

# Restore original modules to prevent polluting other tests in the suite
for mod in ['pandas_ta', 'advanced_ta']:
    if mod in original_modules:
        sys.modules[mod] = original_modules[mod]
    else:
        del sys.modules[mod]


def test_evolutionary_agent_genes():
    # Initialize an agent with known small sizes for testing
    input_size = 10
    hidden_size = 5
    output_size = 2
    agent = EvolutionaryAgent(input_size=input_size, hidden_size=hidden_size, output_size=output_size)

    # Check shape of internal weights initially
    assert agent.weights['W1'].shape == (input_size, hidden_size)
    assert agent.weights['b1'].shape == (1, hidden_size)
    assert agent.weights['W2'].shape == (hidden_size, output_size)
    assert agent.weights['b2'].shape == (1, output_size)

    # Calculate expected total size
    expected_size = (input_size * hidden_size) + hidden_size + (hidden_size * output_size) + output_size

    # Test get_genes
    genes = agent.get_genes()
    assert genes.shape == (expected_size,)

    # Generate new random genes
    new_genes = np.random.randn(expected_size)

    # Test set_genes
    agent.set_genes(new_genes)

    # Test that the shapes of the internal weights remain the same
    assert agent.weights['W1'].shape == (input_size, hidden_size)
    assert agent.weights['b1'].shape == (1, hidden_size)
    assert agent.weights['W2'].shape == (hidden_size, output_size)
    assert agent.weights['b2'].shape == (1, output_size)

    # Test that get_genes returns exactly what we just set
    retrieved_genes = agent.get_genes()
    np.testing.assert_array_almost_equal(retrieved_genes, new_genes)

def test_set_genes_wrong_shape():
    agent = EvolutionaryAgent(input_size=10, hidden_size=5, output_size=2)
    wrong_genes = np.random.randn(10) # Wrong size

    with pytest.raises(ValueError):
        agent.set_genes(wrong_genes)

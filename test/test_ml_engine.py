import pytest
import numpy as np

# We'll patch sys.modules only within a context or fixture, but actually
# we just installed the dependencies, so we can import directly.
from myra_app.ml_engine import EvolutionaryAgent

def test_get_genes():
    """Test get_genes flattens weights properly using actual numpy arrays."""
    agent = EvolutionaryAgent(input_size=2, hidden_size=2, output_size=2)

    agent.weights = {
        'b1': np.array([[1.0, 2.0]]),
        'b2': np.array([[3.0, 4.0]]),
        'W1': np.array([[5.0, 6.0], [7.0, 8.0]]),
        'W2': np.array([[9.0, 10.0], [11.0, 12.0]])
    }

    genes = agent.get_genes()

    # Keys sorted alphabetically: W1, W2, b1, b2
    expected = np.array([5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 1.0, 2.0, 3.0, 4.0])

    np.testing.assert_array_equal(genes, expected)

def test_set_genes():
    """Test set_genes restores weights properly using actual numpy arrays."""
    agent = EvolutionaryAgent(input_size=2, hidden_size=2, output_size=2)

    # Initialize with zeros to ensure it changes
    agent.weights = {
        'W1': np.zeros((2, 2)),
        'W2': np.zeros((2, 2)),
        'b1': np.zeros((1, 2)),
        'b2': np.zeros((1, 2))
    }

    genes_to_set = np.array([5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 1.0, 2.0, 3.0, 4.0])
    agent.set_genes(genes_to_set)

    np.testing.assert_array_equal(agent.weights['W1'], np.array([[5.0, 6.0], [7.0, 8.0]]))
    np.testing.assert_array_equal(agent.weights['W2'], np.array([[9.0, 10.0], [11.0, 12.0]]))
    np.testing.assert_array_equal(agent.weights['b1'], np.array([[1.0, 2.0]]))
    np.testing.assert_array_equal(agent.weights['b2'], np.array([[3.0, 4.0]]))

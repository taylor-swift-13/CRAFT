__all__ = [
    "ExampleSampler",
    "ExampleSet",
    "NEGATIVE_SAMPLER_MODES",
    "NEGATIVE_SCHEMA_VERSION",
]


def __getattr__(name):
    """Lazily expose sampler classes without pre-importing the CLI module."""
    if name in __all__:
        from .example_sampler import (
            ExampleSampler,
            ExampleSet,
            NEGATIVE_SAMPLER_MODES,
            NEGATIVE_SCHEMA_VERSION,
        )

        return {
            "ExampleSampler": ExampleSampler,
            "ExampleSet": ExampleSet,
            "NEGATIVE_SAMPLER_MODES": NEGATIVE_SAMPLER_MODES,
            "NEGATIVE_SCHEMA_VERSION": NEGATIVE_SCHEMA_VERSION,
        }[name]
    raise AttributeError(name)

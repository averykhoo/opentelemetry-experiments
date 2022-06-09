import inspect
import json
import logging
from functools import reduce
from functools import wraps
from typing import Any
from typing import Dict
from typing import Optional

import fastapi
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.export import ConsoleSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME

__version__ = '0.1'

# write IDs as 0xBEEF instead of BEEF so it matches the trace json exactly
from get_function_name import get_function_name

LOGGING_FORMAT_VERBOSE = (
    '%(asctime)s '
    '%(levelname)-8s '
    '[%(name)s] '
    '[%(filename)s:%(funcName)s:%(lineno)d] '
    '[trace_id=0x%(otelTraceID)s span_id=0x%(otelSpanID)s resource.service.name=%(otelServiceName)s] '
    '- %(message)s'
)
LOGGING_FORMAT_MINIMAL = (
    '%(levelname)-8s '
    '%(otelServiceName)s '
    '[00-%(otelTraceID)s-%(otelSpanID)s-01] '
    '[%(name)s:%(module)s:%(funcName)s] '
    '%(message)s'
)

# init tracer
trace.set_tracer_provider(tp := TracerProvider(resource=Resource.create({SERVICE_NAME: 'test-service-name'})))
tp.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter(formatter=lambda span: f'{span.to_json(indent=None)}\n')))
tracer = trace.get_tracer(__name__, __version__)


def instrument_decorate(func):
    if inspect.iscoroutinefunction(func):
        @wraps(func)
        async def wrapped(*args, **kwargs):
            with tracer.start_as_current_span(get_function_name(func)):
                out = await func(*args, **kwargs)
                return out

    else:
        @wraps(func)
        def wrapped(*args, **kwargs):
            with tracer.start_as_current_span(get_function_name(func)):
                out = func(*args, **kwargs)
                return out

    return wrapped


@instrument_decorate
def instrument_logging(*, verbose: bool = True):
    # re-instrument if called
    _instrumentor = LoggingInstrumentor()
    if _instrumentor.is_instrumented_by_opentelemetry:
        _instrumentor.uninstrument()
    _instrumentor.instrument(set_logging_format=False)

    # force overwrite of logging basic config since their instrumentor doesn't do it correctly
    logging.basicConfig(format=LOGGING_FORMAT_VERBOSE if verbose else LOGGING_FORMAT_MINIMAL,
                        level=logging.DEBUG if verbose else logging.INFO,
                        force=True,
                        )


@instrument_decorate
def instrument_fastapi(app: fastapi.FastAPI):
    with tracer.start_as_current_span('instrument_fastapi'):
        # first instrument logging, if not already instrumented
        if not LoggingInstrumentor().is_instrumented_by_opentelemetry:
            instrument_logging()

        # instrument the app
        FastAPIInstrumentor.instrument_app(app)

        # instrument requests as well
        try:
            import requests
            RequestsInstrumentor().instrument()
        except ImportError:
            pass

        # return app
        return app


def logging_tree():
    root: Dict[Optional[str], Any] = dict()  # {...: logging.root}
    for logger_name in sorted(logging.root.manager.loggerDict.keys()):
        logger = logging.root.manager.loggerDict[logger_name]
        if isinstance(logger, logging.Logger):
            reduce(lambda node, name: node.setdefault(name, {}), logger_name.split('.'), root)  # [...] = logger
    return root


if __name__ == '__main__':
    print(json.dumps(logging_tree(), indent=4))
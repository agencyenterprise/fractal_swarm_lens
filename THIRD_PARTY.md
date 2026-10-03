# Third-party material

AI Village records and model-response artifacts are from [AI Digest / AI Village](https://huggingface.co/datasets/aidigestorg/ai-village). Source revision and row references are retained. The source dataset's access conditions and license govern that material independently of the framework code.

Provider SVG icons are from [LobeHub Icons](https://github.com/lobehub/lobe-icons), distributed under the [included MIT license](src/swarm_lens/web/logos/LICENSE). Provider names and logos identify the agents' model families; they do not imply endorsement.

ACIArena is included as a pinned Git submodule at `vendor/aciarena`, from [Greysahy/ACIArena](https://github.com/Greysahy/aciarena). Its source, attack fixtures, and datasets remain governed by the upstream repository's license and terms. The benchmark wrapper in `examples/aciarena` does not modify upstream code.

MAST is pinned at `vendor/mast`, from [multi-agent-systems-failure-taxonomy/MAST](https://github.com/multi-agent-systems-failure-taxonomy/MAST), commit `a70542e541b2104ef8fcd785778179e173fb8d70`. The packaged `observability/mast/assets.json` contains its notebook prompt, definitions, and examples, extracted without executing upstream code. They are attributed to Cemri et al., *Why Do Multi-Agent LLM Systems Fail?* (2025). Upstream material retains its original terms; inclusion does not assign it the framework's license. The integration records source hashes and documents the upstream verification-category label mismatch.

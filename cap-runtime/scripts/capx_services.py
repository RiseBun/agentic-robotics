"""Start official services; SAM3 reads a hash-verified local checkpoint."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

root = Path(os.environ.get('CAP_PROJECT_ROOT', Path(__file__).resolve().parents[2])).resolve()
repo = root / 'third_party/cap-x'
os.environ.setdefault('ROBOT_DESCRIPTIONS_CACHE', str(root / 'cache/robot_descriptions'))
sys.path.insert(0, str(repo))
os.environ.setdefault('JAX_PLATFORMS', 'cpu')
os.environ.setdefault('XLA_PYTHON_CLIENT_PREALLOCATE', 'false')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('service', choices=['sam3', 'grasp', 'pyroki'])
    parser.add_argument('--port', type=int)
    parser.add_argument('--deterministic-seed', type=int, help='Evaluation only: seed GraspNet per input request')
    args = parser.parse_args()
    os.chdir(repo)
    if args.service == 'sam3':
        checkpoint = root / 'models/sam3/sam3.pt'
        sha = hashlib.sha256()
        with checkpoint.open('rb') as file:
            for block in iter(lambda: file.read(8 * 1024 * 1024), b''):
                sha.update(block)
        if sha.hexdigest() != '9999e2341ceef5e136daa386eecb55cb414446a00ac2b55eb2dfd2f7c3cf8c9e':
            raise RuntimeError('SAM3 SHA256 mismatch')
        import sam3.model_builder as builder
        # Preserve official architecture and preprocessing; replace download transport only.
        builder.download_ckpt_from_hf = lambda: str(checkpoint)
        original_load = builder._load_checkpoint
        def checked_load(model, path):
            original_state_load = model.load_state_dict
            def audited_load(*positional, **kwargs):
                result = original_state_load(*positional, **kwargs)
                if result.missing_keys or result.unexpected_keys:
                    raise RuntimeError(f'Incomplete SAM3 checkpoint: {result}')
                print(json.dumps({'sam3_state_dict_complete': True,
                                  'parameter_count': sum(p.numel() for p in model.parameters()),
                                  'sha256': sha.hexdigest()}), flush=True)
                return result
            model.load_state_dict = audited_load
            try:
                return original_load(model, path)
            finally:
                model.load_state_dict = original_state_load
        builder._load_checkpoint = checked_load
        from capx.serving.launch_sam3_server import main as launch
        launch(device='cuda', port=args.port or 8114, host='127.0.0.1')
    elif args.service == 'grasp':
        import capx.serving.launch_contact_graspnet_server as service
        if args.deterministic_seed is not None:
            import random
            import numpy as np
            import torch
            import functools
            torch.use_deterministic_algorithms(True)
            torch.backends.cudnn.benchmark = False
            torch.backends.cudnn.deterministic = True
            def seeded(original):
                @functools.wraps(original)
                def invoke(request):
                    serialized = request.model_dump_json()
                    seed = int.from_bytes(hashlib.sha256(
                        (str(args.deterministic_seed) + ':' + serialized).encode()).digest()[:4], 'little')
                    random.seed(seed)
                    np.random.seed(seed)
                    torch.manual_seed(seed)
                    torch.cuda.manual_seed_all(seed)
                    print(json.dumps({'grasp_request_seed': seed,
                                      'input_sha256': hashlib.sha256(serialized.encode()).hexdigest()}), flush=True)
                    return original(request)
                return invoke
            for name in ['_do_plan', '_do_plan_evenly', '_do_plan_point_clouds']:
                setattr(service, name, seeded(getattr(service, name)))
        service.main(device='cuda', port=args.port or 8115, host='127.0.0.1')
    else:
        from capx.serving.launch_pyroki_server import main as launch
        launch(port=args.port or 8116, host='127.0.0.1')

if __name__ == '__main__':
    main()

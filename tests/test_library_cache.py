import json
from pathlib import Path
import shutil
import tempfile
import unittest
from producer.library_cache import identity,eligible,verify
from producer.sources import sha256
from producer.resources import profile,GIB


class LibraryCacheTests(unittest.TestCase):
    def test_only_material_library_inputs_invalidate_reuse(self):
        repository=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);project=root/'project';project.mkdir()
            shutil.copytree(repository/'containers/native',project/'containers/native',ignore=shutil.ignore_patterns('__pycache__'))
            (project/'producer').mkdir();shutil.copy2(repository/'producer/resources.py',project/'producer/resources.py')
            shutil.copy2(repository/'source-lock.json',project/'source-lock.json')
            source=root/'source';source.mkdir();(source/'library.c').write_text('int value(void) { return 1; }\n')
            dependency=root/'dependency.deb';dependency.write_bytes(b'first dependency payload')
            job={'name':'libfixture','source':'public/libfixture'}
            args=(project,job,source,[dependency],'amd64','sha256:'+'1'*64)
            initial=identity(*args)[0]['input_id']
            (project/'VERSION').write_text('v2026.03.1')
            (project/'guest').mkdir();(project/'guest/first_boot.py').write_text('updated guest UI')
            self.assertEqual(identity(*args)[0]['input_id'],initial)
            dependency.write_bytes(b'changed dependency ABI')
            self.assertNotEqual(identity(*args)[0]['input_id'],initial)
            dependency.write_bytes(b'first dependency payload')
            (source/'library.c').write_text('int value(void) { return 2; }\n')
            self.assertNotEqual(identity(*args)[0]['input_id'],initial)
            self.assertFalse(eligible({'name':'kernel','recipe':'kernel'}))
            self.assertFalse(eligible({'name':'vyos-1x','recipe':'vyos-1x'}))

    def test_corrupted_or_extra_cache_content_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);expected={'input_id':'fixed','job':{'name':'libfixture'},'architecture':'amd64'}
            (root/'context.json').write_text(json.dumps(expected));(root/'fixture.deb').write_bytes(b'package bytes')
            files=[{'name':name,'sha256':sha256(root/name)} for name in ['context.json','fixture.deb']]
            receipt={**expected,'files':files,'packages':[{'name':'fixture.deb','sha256':sha256(root/'fixture.deb')}]}
            (root/'receipt.json').write_text(json.dumps(receipt));verify(root,expected)
            (root/'unexpected.key').write_text('untracked private state')
            with self.assertRaisesRegex(ValueError,'Uninventoried'):verify(root,expected)
            (root/'unexpected.key').unlink();(root/'fixture.deb').write_bytes(b'changed package')
            with self.assertRaisesRegex(ValueError,'missing or differs'):verify(root,expected)

    def test_parallelism_respects_cpu_and_memory_together(self):
        hosted=profile(4,8*GIB);dedicated=profile(8,6*GIB);small=profile(2,2*GIB)
        self.assertEqual(hosted['compile_jobs'],4)
        self.assertEqual(dedicated['kernel_jobs'],6)
        self.assertEqual(dedicated['compile_jobs'],4)
        self.assertEqual(small['compile_jobs'],1)
        self.assertLessEqual(small['go_memory_limit_mib']*(small['go_jobs']+1),2048)

"""Interpret a small Python subset; generated code never reaches Python exec/eval."""
import ast
import operator


class BoundaryError(ValueError):
    def __init__(self, message, evidence=None):
        self.evidence = evidence
        super().__init__(message)


class Interpreter:
    OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.USub: operator.neg, ast.UAdd: operator.pos,
           ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
           ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}
    NODES = (ast.Module, ast.Assign, ast.Expr, ast.If, ast.For, ast.Pass,
             ast.Name, ast.Load, ast.Store, ast.Constant, ast.List, ast.Tuple,
             ast.Dict, ast.Subscript, ast.Call, ast.keyword, ast.BinOp, ast.UnaryOp,
             ast.Compare, ast.BoolOp, ast.And, ast.Or, ast.Not, *OPS.keys())
    BUILTINS = {'len': len, 'min': min, 'max': max, 'abs': abs, 'float': float,
                'int': int, 'list': list, 'range': lambda n: range(min(16, max(0, int(n))))}

    def __init__(self, tools, checkpoint):
        self.tools, self.checkpoint = tools, checkpoint
        self.scope = {}
        self.operations = 0

    def run(self, source):
        tree = self.validate(source)
        self.block(tree.body)
        return self.scope

    def validate(self, source):
        if not isinstance(source, str) or not source.strip() or len(source) > 12000:
            raise BoundaryError("Code must contain 1..12000 characters")
        tree = ast.parse(source)
        if len(list(ast.walk(tree))) > 1500:
            raise BoundaryError("Code is too large")
        for node in ast.walk(tree):
            if not isinstance(node, self.NODES):
                raise BoundaryError("Unsupported syntax: " + type(node).__name__)
            if isinstance(node, ast.Name) and (node.id.startswith('_') or node.id in {'env', 'obs', 'APIS'}):
                raise BoundaryError("Private names are forbidden")
            if isinstance(node, ast.Constant) and not isinstance(node.value, (str, int, float, bool, type(None))):
                raise BoundaryError("Unsupported literal")
        # Check the entire segment before its first physical side effect. A
        # typo late in a program must not execute the earlier robot commands.
        self._bindings_block(tree.body, set(self.scope))
        return tree

    def _binding_error(self, message, node):
        raise BoundaryError(f'{message} (line {getattr(node, "lineno", "?")})')

    def _bindings_expr(self, node, bound):
        if isinstance(node, ast.Name):
            if node.id not in bound:
                raise BoundaryError(f'Unbound local name {node.id!r}; define it before use (line {node.lineno})',
                    dict(kind='unbound_name', name=node.id, line=node.lineno, column=node.col_offset+1,
                         available_locals=sorted(bound)[:32],
                         scope='Names bound at this expression; no inferred replacement or robot action'))
        elif isinstance(node, ast.Call):
            if (not isinstance(node.func, ast.Name) or any(k.arg is None for k in node.keywords)):
                self._binding_error('Only registered direct function calls are allowed', node)
            if node.func.id not in self.tools and node.func.id not in self.BUILTINS:
                self._binding_error('Unknown function: '+node.func.id, node)
            for arg in node.args:
                self._bindings_expr(arg, bound)
            for keyword in node.keywords:
                self._bindings_expr(keyword.value, bound)
        else:
            if isinstance(node, ast.Dict) and any(k is None for k in node.keys):
                self._binding_error('Dictionary expansion is unsupported', node)
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    self._bindings_expr(child, bound)

    def _bindings_target(self, target, bound):
        if isinstance(target, ast.Name):
            if target.id in self.tools:
                self._binding_error('Cannot overwrite an API: '+target.id, target)
            bound.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._bindings_target(item, bound)
        elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
            self._bindings_expr(target.value, bound)
            self._bindings_expr(target.slice, bound)
        else:
            self._binding_error('Invalid assignment target', target)

    def _bindings_block(self, nodes, incoming):
        bound = set(incoming)
        for node in nodes:
            self.checkpoint()
            if isinstance(node, ast.Assign):
                self._bindings_expr(node.value, bound)
                for target in node.targets:
                    self._bindings_target(target, bound)
            elif isinstance(node, ast.Expr):
                self._bindings_expr(node.value, bound)
            elif isinstance(node, ast.If):
                self._bindings_expr(node.test, bound)
                yes = self._bindings_block(node.body, bound)
                no = self._bindings_block(node.orelse, bound)
                bound = (yes if node.test.value else no) if isinstance(node.test, ast.Constant) else yes & no
            elif isinstance(node, ast.For):
                self._bindings_expr(node.iter, bound)
                nonempty = False
                if isinstance(node.iter, (ast.List, ast.Tuple)):
                    if len(node.iter.elts) > 16:
                        self._binding_error('Loops need at most 16 items', node)
                    nonempty = bool(node.iter.elts)
                elif (isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Name)
                      and node.iter.func.id == 'range' and 'range' not in self.tools
                      and len(node.iter.args) == 1 and not node.iter.keywords
                      and isinstance(node.iter.args[0], ast.Constant) and isinstance(node.iter.args[0].value, int)):
                    nonempty = node.iter.args[0].value > 0
                body = set(bound)
                self._bindings_target(node.target, body)
                body = self._bindings_block(node.body, body)
                # No break exists in this language, so else always runs. The
                # loop's newly introduced names survive only if it must run.
                bound = self._bindings_block(node.orelse, body if nonempty else bound)
        return bound

    def block(self, nodes):
        for node in nodes:
            self.checkpoint()
            if isinstance(node, ast.Assign):
                value = self.expr(node.value)
                for target in node.targets:
                    self.assign(target, value)
            elif isinstance(node, ast.Expr):
                self.expr(node.value)
            elif isinstance(node, ast.If):
                self.block(node.body if self.expr(node.test) else node.orelse)
            elif isinstance(node, ast.For):
                values = self.expr(node.iter)
                if not isinstance(values, (list, tuple, range)) or len(values) > 16:
                    raise BoundaryError("Loops need at most 16 items")
                for value in values:
                    self.assign(node.target, value)
                    self.block(node.body)
                self.block(node.orelse)
            elif not isinstance(node, ast.Pass):
                raise BoundaryError("Unsupported statement")

    def assign(self, target, value):
        if isinstance(target, ast.Name):
            if target.id in self.tools:
                raise BoundaryError("Cannot overwrite an API")
            self.scope[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            if len(target.elts) != len(value):
                raise BoundaryError("Unpack mismatch")
            for item, element in zip(target.elts, value):
                self.assign(item, element)
        elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
            self.scope[target.value.id][self.expr(target.slice)] = value
        else:
            raise BoundaryError("Invalid assignment")

    def expr(self, node):
        self.operations += 1
        if self.operations > 4000:
            raise BoundaryError("Expression budget exceeded")
        self.checkpoint()
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return self.scope[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            return [self.expr(x) for x in node.elts]
        if isinstance(node, ast.Dict):
            return {self.expr(k): self.expr(v) for k, v in zip(node.keys, node.values)}
        if isinstance(node, ast.Subscript):
            # Evaluate calls outside this handler: an API's own exception is
            # not a generated-program indexing error.
            container, key = self.expr(node.value), self.expr(node.slice)
            try:
                return container[key]
            except (KeyError, IndexError, TypeError) as exc:
                fields = [str(k)[:80] for k in container][:16] if isinstance(container, dict) else []
                requested = key[:128] if isinstance(key, str) else key if isinstance(key, (int, float, bool, type(None))) else type(key).__name__
                diagnostic = dict(kind='invalid_subscript', line=node.lineno, column=node.col_offset+1,
                    container_type=type(container).__name__, requested_key=requested, available_fields=fields)
                if isinstance(container, dict):
                    message = f'Dictionary has no valid key {requested!r}; use a named public field. Available fields: {", ".join(fields)}'
                else:
                    message = f'Invalid index {requested!r} for {type(container).__name__}'
                    if isinstance(container, (list, tuple, str)):
                        diagnostic['length'] = len(container)
                        message += f' of length {len(container)}'
                raise BoundaryError(f'{message} (line {node.lineno})', diagnostic) from exc
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or any(k.arg is None for k in node.keywords):
                raise BoundaryError("Only registered function calls are allowed")
            fn = self.tools.get(node.func.id, self.BUILTINS.get(node.func.id))
            if fn is None:
                raise BoundaryError("Unknown function: " + node.func.id)
            return fn(*[self.expr(a) for a in node.args],
                      **{k.arg: self.expr(k.value) for k in node.keywords})
        if isinstance(node, ast.BinOp):
            a, b = self.expr(node.left), self.expr(node.right)
            if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
                raise BoundaryError("Arithmetic is scalar only; edit vector elements explicitly")
            value = self.OPS[type(node.op)](a, b)
            if abs(value) > 1e6:
                raise BoundaryError("Numeric limit exceeded")
            return value
        if isinstance(node, ast.UnaryOp):
            value = self.expr(node.operand)
            return not value if isinstance(node.op, ast.Not) else self.OPS[type(node.op)](value)
        if isinstance(node, ast.BoolOp):
            for value in node.values:
                result = self.expr(value)
                if isinstance(node.op, ast.And) and not result or isinstance(node.op, ast.Or) and result:
                    return result
            return result
        if isinstance(node, ast.Compare):
            left = self.expr(node.left)
            for op, right in zip(node.ops, node.comparators):
                value = self.expr(right)
                if not self.OPS[type(op)](left, value):
                    return False
                left = value
            return True
        raise BoundaryError("Unsupported expression: " + type(node).__name__)

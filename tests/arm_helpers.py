"""Offline readers for compiled ARM expressions used by infrastructure tests.

Only the supported test subset is interpreted; unknown functions fail explicitly.
"""
import base64
import hashlib
import json
import re


class ArmExpression:
    """このAPI定義のARM式だけを読む。Pythonのevalやネットワークは使用しない。

    コンパイラーの出力を値へ展開することで、文字列の存在確認だけでは見逃す
    辞書キーの誤りや、別プロトコルへの誤適用も検査する。未知の構文は失敗させる。
    """

    def __init__(self, expression):
        self.tokens = re.findall(r"'(?:''|[^'])*'|[A-Za-z_][A-Za-z_0-9]*\.[A-Za-z_][A-Za-z_0-9]*(?=\()|[A-Za-z_][A-Za-z_0-9]*|\d+|[().,\[\]]", expression[1:-1])
        self.position = 0
        self.tree = self.parse()
        if self.position != len(self.tokens):
            raise AssertionError("Unsupported trailing ARM expression")

    def pop(self, expected=None):
        token = self.tokens[self.position]
        self.position += 1
        if expected is not None and token != expected:
            raise AssertionError("Unexpected ARM token")
        return token

    def parse(self):
        token = self.pop()
        if token.startswith("'"):
            tree = ("literal", token[1:-1].replace("''", "'"))
        elif token.isdigit():
            tree = ("literal", int(token))
        else:
            self.pop("(")
            arguments = []
            while self.tokens[self.position] != ")":
                arguments.append(self.parse())
                if self.tokens[self.position] != ",":
                    break
                self.pop(",")
            self.pop(")")
            tree = ("call", token, arguments)
        while self.position < len(self.tokens) and self.tokens[self.position] in (".", "["):
            suffix = self.pop()
            if suffix == ".":
                tree = ("get", tree, ("literal", self.pop()))
            else:
                tree = ("get", tree, self.parse())
                self.pop("]")
        return tree

    def evaluate(self, parameters, index, tree=None, bindings=None):
        tree = self.tree if tree is None else tree
        bindings = {} if bindings is None else bindings
        value = lambda item: self.evaluate(parameters, index, item, bindings)
        if tree[0] == "literal":
            return tree[1]
        if tree[0] == "get":
            return value(tree[1])[value(tree[2])]
        _, name, arguments = tree
        # ARMのifは選んだ枝だけを評価する。HTTP APIにmcpProxyが無くても正しい。
        if name == "if":
            return value(arguments[1] if value(arguments[0]) else arguments[2])
        if name == "lambda":
            parameter, body = arguments
            return lambda item: self.evaluate(parameters, index, body, {**bindings, value(parameter): item})
        values = [value(argument) for argument in arguments]
        if name in ("true", "false"): return name == "true"
        if name == "reference":
            return {"identity":{"principalId":"77777777-7777-4777-8777-777777777777"}}
        if name == "parameters":
            return parameters[values[0]]
        if name == "variables":
            return parameters["__variables"][values[0]]
        if name == "copyIndex":
            return index
        if name == "lambdaVariables":
            return bindings[values[0]]
        if name == "createObject":
            return dict(zip(values[::2], values[1::2]))
        if name == "createArray":
            return values
        if name == "equals":
            return values[0] == values[1]
        if name == "not":
            return not values[0]
        if name == "format":
            return values[0].format(*values[1:])
        if name == "__bicep.xmlText":
            return values[0].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        if name == "filter":
            return [item for item in values[0] if values[1](item)]
        if name == "first":
            return values[0][0]
        if name == "union":
            return {key: item for mapping in values for key, item in mapping.items()}
        if name == "tryGet":
            value = values[0]
            for key in values[1:]:
                if value is None: return None
                value = value.get(key) if isinstance(value, dict) else value[key] if 0 <= key < len(value) else None
            return value
        if name == "coalesce":
            return next(item for item in values if item is not None)
        if name == "toObject":
            collection, key, item = values
            return {key(entry): item(entry) for entry in collection}
        raise AssertionError("Unsupported ARM function: " + name)


class PolicyExpression(ArmExpression):
    """実コンパイル後の変数・XML置換を展開し、Azureへ渡す最終XMLを読む。"""

    def __init__(self, expression, variables, cache=None, functions=None):
        super().__init__(expression)
        self.variables = variables
        self.cache = {} if cache is None else cache
        self.functions = functions or []

    def resolve(self, value, parameters, index=0):
        if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
            return PolicyExpression(value, self.variables, self.cache, self.functions).evaluate(parameters, index)
        return value

    def evaluate(self, parameters, index=0, tree=None, bindings=None):
        parameters = {"entraTenantId": "99999999-9999-4999-8999-999999999999", **parameters}
        tree = self.tree if tree is None else tree
        bindings = {} if bindings is None else bindings
        if tree[0] != "call":
            return super().evaluate(parameters, index, tree, bindings)
        _, name, arguments = tree
        if name.startswith('__bicep.') and self.functions:
            # UDFの実コンパイル結果を解釈します。実装と同じ結果を返す別のPython関数は作りません。
            namespace, member = name.split('.', 1)
            function = next(item for item in self.functions if item['namespace'] == namespace)['members'][member]
            values = [self.evaluate(parameters, index, argument, bindings) for argument in arguments]
            supplied = dict(zip((item['name'] for item in function['parameters']), values))
            output = function['output']['value']
            if isinstance(output, dict):
                return {key: PolicyExpression(value, self.variables, self.cache, self.functions).evaluate(supplied)
                        if isinstance(value, str) and value.startswith('[') else value for key, value in output.items()}
            return PolicyExpression(output, self.variables, self.cache, self.functions).evaluate(supplied)
        if name in ("true", "false"): return name == "true"
        if name == "or": return any(self.evaluate(parameters, index, argument, bindings) for argument in arguments)
        if name == "and":
            return all(self.evaluate(parameters, index, argument, bindings) for argument in arguments)
        extra = {"variables", "replace", "base64", "string", "join", "items", "map", "contains", "length", "flatten", "union", "startsWith", "greater", "empty", "split", "first", "last", "substring", "endsWith", "or", "uniqueString"}
        if name not in extra:
            return super().evaluate(parameters, index, tree, bindings)
        values = [self.evaluate(parameters, index, argument, bindings) for argument in arguments]
        if name == "variables":
            key = values[0]
            if key not in self.cache:
                if key in self.variables:
                    self.cache[key] = self.resolve(self.variables[key], parameters, index)
                else:
                    definition = next(item for item in self.variables["copy"] if item["name"] == key)
                    self.cache[key] = [self.resolve(definition["input"], parameters, number)
                                       for number in range(self.resolve(definition["count"], parameters))]
            return self.cache[key]
        if name == "replace": return values[0].replace(values[1], values[2])
        if name == "base64": return base64.b64encode(values[0].encode()).decode()
        # ARMのhash自体は再実装しない。プラットフォーム関数の固定長代替値で、
        # resource名とXML参照が同じ入力tupleに結び付くことを検査する。
        if name == "uniqueString": return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()[:13]
        if name == "string": return json.dumps(values[0], ensure_ascii=False, separators=(",", ":"))
        if name == "join": return values[1].join(values[0])
        if name == "items": return [{"key": key, "value": value} for key, value in sorted(values[0].items())]
        if name == "map": return [values[1](item) for item in values[0]]
        if name == "contains": return values[1] in values[0]
        if name == "length": return len(values[0])
        if name == "split": return values[0].split(values[1])
        if name == "first": return values[0][0]
        if name == "last": return values[0][-1]
        if name == "substring": return values[0][values[1]:] if len(values) == 2 else values[0][values[1]:values[1]+values[2]]
        if name == "empty": return not values[0]
        if name == "flatten": return [item for group in values[0] for item in group]
        if name == "startsWith": return values[0].startswith(values[1])
        if name == "endsWith": return values[0].endswith(values[1])
        if name == "greater": return values[0] > values[1]
        if name == "union":
            if isinstance(values[0], dict):
                return {key: item for mapping in values for key, item in mapping.items()}
            result = []
            for group in values:
                for item in group:
                    if item not in result: result.append(item)
            return result
        raise AssertionError("Unsupported policy ARM function: " + name)


class DiscoveryExpression(PolicyExpression):
    """既存の ARM 読取器に、metadata で使う環境値と配列結合だけを補う。

    実装と同じ結果を直接返す mock は作らず、コンパイラーが生成した UDF の
    本文・引数・lambda を再帰評価する。未知の ARM 関数は従来どおり失敗させる。
    """

    def resolve(self, value, parameters, index=0):
        # ARM の object / array は各要素を評価し、文字列の式だけを parser へ渡す。
        if isinstance(value, dict):
            return {key: self.resolve(item, parameters, index) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve(item, parameters, index) for item in value]
        if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
            return type(self)(value, self.variables, self.cache, self.functions).evaluate(parameters, index)
        return value

    def evaluate(self, parameters, index=0, tree=None, bindings=None):
        tree = self.tree if tree is None else tree
        bindings = {} if bindings is None else bindings
        if tree[0] == "call":
            _, name, arguments = tree
            if name.startswith("__bicep."):
                namespace, member = name.split(".", 1)
                function = next(item for item in self.functions if item["namespace"] == namespace)["members"][member]
                values = [self.evaluate(parameters, index, argument, bindings) for argument in arguments]
                supplied = dict(zip((item["name"] for item in function["parameters"]), values))
                return self.resolve(function["output"]["value"], supplied, index)
            if name == "environment":
                return {"authentication": {"loginEndpoint": "https://login.microsoftonline.com/"}}
            if name in ("concat", "empty"):
                values = [self.evaluate(parameters, index, argument, bindings) for argument in arguments]
                if name == "empty":
                    return not values[0]
                if isinstance(values[0], list):
                    return [item for group in values for item in group]
                return "".join(values)
        return super().evaluate(parameters, index, tree, bindings)

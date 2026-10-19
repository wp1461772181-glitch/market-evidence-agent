"""Small Decimal arithmetic over inputs present in this turn's accepted sources."""
import ast
import re
import unicodedata
from decimal import Decimal, InvalidOperation, localcontext
from .semantic import _FIGURE, _MAGNITUDE, PERIOD_TEXT, ACCESSION, SOURCE_ID, TASK_ID
# Percent and unit factors only: a power of ten converts a cited value (to 亿 or billions), it adds no figure.
CONSTANTS=frozenset({0,1,100,1000,10**4,10**6,10**8,10**9,10**12})


def calculate(args, citations):
    expression=args['expression']
    if not isinstance(expression,str) or len(expression)>200:raise ValueError('invalid_calculation')
    indexed={c['ordinal']:c for c in citations};values={};parents=[]
    for item in args['inputs']:
        name=item['name'];source=indexed.get(item['source_ordinal'])
        if not re.fullmatch(r'[a-z]',name) or name in values or not source:raise ValueError('calculation_input_unbound')
        text=unicodedata.normalize('NFKC',str(source.get('quote',''))).replace('萬','万').replace('億','亿')
        text=PERIOD_TEXT.sub(' ',ACCESSION.sub(' ',SOURCE_ID.sub(' ',TASK_ID.sub(' ',text))))
        text=re.sub(r'(?<=\d),(?=\d{3}(?!\d))','',text)
        numbers={Decimal(n)*Decimal(str(_MAGNITUDE.get(unit.lower(),1))) for n,unit in _FIGURE.findall(text)}
        try:value=Decimal(item['value'])
        except (InvalidOperation,TypeError):raise ValueError('invalid_calculation')
        if not value.is_finite() or value not in numbers:raise ValueError('calculation_input_unbound')
        values[name]=value;parents.append(item['source_ordinal'])
    if not values or len(values)>8:raise ValueError('invalid_calculation')
    def evaluate(node):
        if isinstance(node,ast.Name) and node.id in values:return values[node.id]
        if isinstance(node,ast.Constant) and type(node.value) is int and node.value in CONSTANTS:return Decimal(node.value)
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            value=evaluate(node.operand);return value if isinstance(node.op,ast.UAdd) else -value
        if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
            left,right=evaluate(node.left),evaluate(node.right)
            if isinstance(node.op,ast.Add):return left+right
            if isinstance(node.op,ast.Sub):return left-right
            if isinstance(node.op,ast.Mult):return left*right
            return left/right
        raise ValueError('invalid_calculation')
    try:
        tree=ast.parse(expression,mode='eval')
        if len(list(ast.walk(tree)))>80:raise ValueError('invalid_calculation')
        if {n.id for n in ast.walk(tree) if isinstance(n,ast.Name)}!=set(values):raise ValueError('invalid_calculation')
        decimals=args.get('decimals',2)
        if type(decimals) is not int or not 0<=decimals<=8:raise ValueError('invalid_calculation')
        with localcontext() as ctx:
            ctx.prec=50
            value=evaluate(tree.body)
            if not value.is_finite() or abs(value)>Decimal('1e24'):raise ValueError('invalid_calculation')
            value=value.quantize(Decimal(1).scaleb(-decimals))
    except (SyntaxError,ArithmeticError,RecursionError) as exc:raise ValueError('invalid_calculation') from exc
    return {'value':str(value),'expression':expression,'inputs':args['inputs'],'parents':sorted(set(parents)),'derived':True}


def bindable_values(citations, args):
    """The numbers a model may bind from the cited sources, as strings.

    ``calculation_input_unbound`` is unhelpful on its own: the model then guesses another value
    and the same call fails again. Handing back what the source actually contains ends the loop.
    """
    indexed={c['ordinal']:c for c in citations or []}
    found={}
    for item in args.get('inputs') or []:
        ordinal=item.get('source_ordinal') if isinstance(item,dict) else None
        source=indexed.get(ordinal)
        if not source:continue
        text=unicodedata.normalize('NFKC',str(source.get('quote',''))).replace('萬','万').replace('億','亿')
        text=PERIOD_TEXT.sub(' ',ACCESSION.sub(' ',SOURCE_ID.sub(' ',TASK_ID.sub(' ',text))))
        text=re.sub(r'(?<=\d),(?=\d{3}(?!\d))','',text)
        values=[str(Decimal(n)*Decimal(str(_MAGNITUDE.get(unit.lower(),1)))) for n,unit in _FIGURE.findall(text)]
        if values:found[str(ordinal)]=values[:12]
    return found
